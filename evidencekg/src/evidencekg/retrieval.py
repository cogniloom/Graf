import base64
import json

from .db import dump, sha
from .relationships import explain, link_sources, reason, verify_posting
from .snapshots import fts_name


class API:
    def __init__(self, store):
        self.store = store

    def page(self, snapshot, scope, rows, cursor=None, limit=100, key=lambda x: x["id"]):
        self.store.snapshot(snapshot)
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        signature = sha(dump(scope))
        after = None
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor))
                if value["snapshot"] != snapshot or value["scope"] != signature:
                    raise ValueError("Cursor scope/snapshot mismatch")
                after = value["after"]
            except Exception as exc:
                raise ValueError("Invalid cursor") from exc
        ordered = sorted(rows, key=key)
        total = len(ordered)
        selected = [r for r in ordered if after is None or key(r) > after]
        items = []
        used = 0
        for row in selected[:limit]:
            size = len(dump(row).encode())
            if used + size > 750000:
                if not items:
                    raise ValueError("One result exceeds response budget; use bounded source/feature reads")
                break
            items.append(row)
            used += size
        nxt = (
            base64.urlsafe_b64encode(
                dump({"snapshot": snapshot, "scope": signature, "after": key(items[-1])}).encode()
            ).decode()
            if len(selected) > len(items)
            else None
        )
        manifest = self.store.manifest(snapshot)
        return dict(
            snapshot_id=snapshot,
            items=items,
            next_cursor=nxt,
            total=None if scope["kind"] == "inventory" and not manifest["inventory_complete"] else total,
            known_matches=total,
            remaining=max(0, len(selected) - len(items)),
            extraction_warnings={
                "count": sum(bool(d["warnings"]) for d in manifest["documents"]),
                "details": "inventory tool enumerates all document warnings without truncation",
            },
            scope=scope,
            inventory_complete=manifest["inventory_complete"],
        )

    def inventory(self, snapshot_id=None, cursor=None, limit=100):
        snapshot = self.store.snapshot(snapshot_id)["id"]
        rows = []
        for document in self.store.manifest(snapshot)["documents"]:
            item = dict(document)
            for field in ("warnings", "artifacts"):
                values = document.get(field, [])
                item[field] = values[:8]
                item[field + "_remaining"] = max(0, len(values) - 8)
                if len(values) > 8:
                    item[field + "_continuation"] = {
                        "tool": "document_details",
                        "snapshot_id": snapshot,
                        "document_version_id": document["document_version_id"],
                        "kind": field,
                    }
            rows.append(item)
        return self.page(
            snapshot, {"kind": "inventory"}, rows, cursor, limit, key=lambda r: r["document_version_id"]
        )

    def sections(self, snapshot_id, document_version_id, cursor=None, limit=100):
        self.store.one(
            "SELECT * FROM (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) WHERE snapshot_id=? AND document_version_id=?",
            (snapshot_id, document_version_id),
        )
        rows = self.store.rows(
            """SELECT s.id,s.extraction_id,s.ordinal,s.char_start,s.char_end,s.locator_json,s.modality,s.status
            FROM segments s JOIN (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) sd ON sd.extraction_id=s.extraction_id WHERE sd.snapshot_id=? AND sd.document_version_id=?""",
            (snapshot_id, document_version_id),
        )
        return self.page(
            snapshot_id, {"kind": "sections", "document": document_version_id}, rows, cursor, limit
        )

    def segment(self, snapshot, sid):
        row = self.store.one(
            """SELECT s.*,e.document_version_id FROM segments s JOIN extractions e ON e.id=s.extraction_id
            JOIN (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) sd ON sd.extraction_id=e.id WHERE sd.snapshot_id=? AND s.id=?""",
            (snapshot, sid),
        )
        row["locators"] = json.loads(row.pop("locator_json"))
        if sha(row["text"]) != row["text_sha"]:
            raise ValueError("Corrupt segment text")
        return row

    def read_segments(self, snapshot_id, segment_ids, max_output_bytes=100000):
        self.store.snapshot(snapshot_id)
        if not 1 <= max_output_bytes <= 1_000_000 or len(segment_ids) > 1000:
            raise ValueError("Invalid read budget")
        result = {
            "snapshot_id": snapshot_id,
            "items": [],
            "remaining_segment_ids": [],
            "scope": "complete requested segments",
        }
        for i, sid in enumerate(segment_ids):
            row = self.segment(snapshot_id, sid)
            if (
                len(
                    dump(
                        {
                            **result,
                            "items": result["items"] + [row],
                            "remaining_segment_ids": segment_ids[i + 1 :],
                        }
                    ).encode()
                )
                > max_output_bytes
            ):
                if not result["items"]:
                    raise ValueError("Single segment exceeds output budget; increase max_output_bytes")
                result["remaining_segment_ids"] = segment_ids[i:]
                break
            result["items"].append(row)
        return result

    def segment_locators(self, snapshot_id, segment_id, cursor=None, limit=100):
        """Complete locator metadata, independently paginated from original text."""
        segment = self.segment(snapshot_id, segment_id)
        rows = [{"ordinal": i, "location": locator} for i, locator in enumerate(segment["locators"])]
        return self.page(
            snapshot_id,
            {"kind": "segment_locators", "segment_id": segment_id},
            rows,
            cursor,
            limit,
            key=lambda row: row["ordinal"],
        )

    def read_original_region(self, snapshot_id, document_version_id, locator):
        if set(locator) - {"artifact", "byte_start", "byte_length"}:
            raise ValueError(
                "Supported locators: artifact name and byte_start/byte_length; page images are named artifacts"
            )
        doc = self.store.one(
            """SELECT d.*,e.artifact_sha FROM (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) sd JOIN document_versions d ON d.id=sd.document_version_id
            JOIN extractions e ON e.id=sd.extraction_id WHERE sd.snapshot_id=? AND d.id=?""",
            (snapshot_id, document_version_id),
        )
        # Byte ranges of preserved originals, or a named extracted/rendered artifact only.
        blob = doc["original_blob_sha"]
        if "artifact" in locator:
            artifact = json.loads(self.store.get(doc["artifact_sha"]))
            choices = {a["name"]: a["blob"] for a in artifact["artifacts"]}
            if locator["artifact"] not in choices:
                raise ValueError("Unknown preserved region artifact")
            blob = choices[locator["artifact"]]
        if not blob:
            raise ValueError("Original unavailable; acquisition gap")
        data = self.store.get(blob)
        start, length = locator.get("byte_start", 0), locator.get("byte_length", 65536)
        if (
            type(start) is not int
            or type(length) is not int
            or start < 0
            or not 1 <= length <= 524288
            or start > len(data)
        ):
            raise ValueError("Invalid region budget")
        end = min(len(data), start + length)
        return dict(
            snapshot_id=snapshot_id,
            document_version_id=document_version_id,
            blob_sha=blob,
            data_base64=base64.b64encode(data[start:end]).decode(),
            byte_start=start,
            byte_end=end,
            total_bytes=len(data),
            next_byte_start=end if end < len(data) else None,
            scope="captured bytes; not a visual review",
        )

    def search(self, snapshot_id, query, mode="lexical", cursor=None, limit=100):
        if not query or len(query) > 4096:
            raise ValueError("Query must contain 1..4096 characters")
        if mode == "lexical":
            # Deliberately support a phrase only, not arbitrary FTS expression syntax.
            expression = '"' + query.replace('"', '""') + '"'
            self.store.snapshot(snapshot_id)
            name = fts_name(snapshot_id)
            rows = self.store.rows(
                f"""SELECT s.id,s.extraction_id,s.text,e.document_version_id,bm25({name}) score
                FROM {name} JOIN segments s ON s.id={name}.segment_id JOIN extractions e ON e.id=s.extraction_id
                JOIN snapshot_documents sd ON sd.extraction_id=e.id WHERE sd.snapshot_id=? AND {name} MATCH ?""",
                (snapshot_id, expression),
            )
            for row in rows:
                row["order_key"] = [row["score"], row["id"]]
            return self.page(
                snapshot_id,
                {
                    "kind": "search",
                    "mode": mode,
                    "query": query,
                    "grammar": "unicode61 phrase; case/diacritic folding; BM25",
                },
                rows,
                cursor,
                limit,
                key=lambda r: r["order_key"],
            )
        if mode != "literal":
            raise ValueError("mode must be lexical or literal")
        # Search canonical extraction, including strings crossing segment boundaries.
        rows = []
        for doc in self.store.rows(
            """SELECT e.* FROM snapshot_documents sd JOIN extractions e ON e.id=sd.extraction_id WHERE sd.snapshot_id=?""",
            (snapshot_id,),
        ):
            text = json.loads(self.store.get(doc["artifact_sha"]))["text"]
            segments = self.store.rows(
                "SELECT * FROM segments WHERE extraction_id=? ORDER BY ordinal", (doc["id"],)
            )
            pos = 0
            while (pos := text.find(query, pos)) >= 0:
                refs = []
                for seg in segments:
                    lo, hi = max(pos, seg["char_start"]), min(pos + len(query), seg["char_end"])
                    if hi > lo:
                        refs.append(
                            dict(
                                segment_id=seg["id"],
                                extraction_id=doc["id"],
                                start=lo - seg["char_start"],
                                end=hi - seg["char_start"],
                                quote=text[lo:hi],
                            )
                        )
                rows.append(
                    dict(
                        id=doc["id"] + ":" + str(pos).zfill(16),
                        document_version_id=doc["document_version_id"],
                        sources=refs,
                        quote=query,
                    )
                )
                pos += 1
        return self.page(
            snapshot_id,
            {
                "kind": "search",
                "mode": mode,
                "query": query,
                "absence_scope": "preserved extracted text only",
            },
            rows,
            cursor,
            limit,
        )

    def neighbours(self, snapshot_id, node_id, relation_types=None, cursor=None, limit=100):
        if node_id.startswith("F"):
            condition, params = "o.feature_id=?", [node_id]
        else:
            self.store.one(
                "SELECT * FROM (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) WHERE snapshot_id=? AND document_version_id=?",
                (snapshot_id, node_id),
            )
            condition = """o.feature_id IN (SELECT o2.feature_id FROM occurrences o2 JOIN segments s2 ON s2.id=o2.segment_id
                JOIN snapshot_documents sd2 ON sd2.extraction_id=s2.extraction_id WHERE sd2.snapshot_id=? AND sd2.document_version_id=?) AND e.document_version_id!=?"""
            params = [snapshot_id, node_id, node_id]
        rows = self.store.rows(
            """SELECT o.*,f.kind,f.namespace,f.canonical_value,e.document_version_id,e.id extraction_id
            FROM snapshot_occurrences so JOIN occurrences o ON o.id=so.occurrence_id JOIN features f ON f.id=o.feature_id
            JOIN segments s ON s.id=o.segment_id JOIN extractions e ON e.id=s.extraction_id WHERE so.snapshot_id=? AND """
            + condition,
            [snapshot_id, *params],
        )
        cache = {}
        for row in rows:
            verify_posting(self.store, row, cache)
        items = [dict(id=r["id"], **reason(r)) for r in rows]
        links = self.store.rows(
            """SELECT l.* FROM snapshot_links sl JOIN explicit_links l ON l.id=sl.link_id
            WHERE sl.snapshot_id=? AND (l.from_node=? OR l.to_node=?)""",
            (snapshot_id, node_id, node_id),
        )
        for link in links:
            items.append(
                dict(
                    id=link["id"],
                    relationship=link["relation_type"],
                    from_node=link["from_node"],
                    to_node=link["to_node"],
                    status=link["status"],
                    derivation=json.loads(link["derivation_json"]),
                    rule_version=link["rule_version"],
                    sources=link_sources(self.store, snapshot_id, link["id"]),
                )
            )
        if relation_types:
            items = [i for i in items if i["relationship"] in relation_types]
        return self.page(
            snapshot_id,
            {"kind": "neighbours", "node": node_id, "relations": relation_types},
            items,
            cursor,
            limit,
        )

    def explain_connection(self, snapshot_id, left_document_id, right_document_id, cursor=None, limit=100):
        items = explain(self.store, snapshot_id, left_document_id, right_document_id)
        for i in items:
            i["id"] = sha(dump(i))
        return self.page(
            snapshot_id,
            {"kind": "explain", "left": left_document_id, "right": right_document_id},
            items,
            cursor,
            limit,
        )

    def related(self, snapshot_id, document_version_id, cursor=None, limit=100):
        """Ranked discovery, separate from complete exact-membership enumeration."""
        import math

        self.store.one(
            "SELECT * FROM (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) WHERE snapshot_id=? AND document_version_id=?",
            (snapshot_id, document_version_id),
        )
        rows = self.store.rows(
            """SELECT DISTINCT e.document_version_id,f.id feature_id,f.kind
            FROM snapshot_occurrences so JOIN occurrences o ON o.id=so.occurrence_id
            JOIN features f ON f.id=o.feature_id JOIN segments s ON s.id=o.segment_id
            JOIN extractions e ON e.id=s.extraction_id WHERE so.snapshot_id=? AND e.document_version_id!=?
            AND o.feature_id IN (SELECT o2.feature_id FROM occurrences o2 JOIN segments s2 ON s2.id=o2.segment_id
                JOIN snapshot_documents sd2 ON sd2.extraction_id=s2.extraction_id WHERE sd2.snapshot_id=? AND sd2.document_version_id=?)""",
            (snapshot_id, document_version_id, snapshot_id, document_version_id),
        )
        total = self.store.db.execute(
            "SELECT count(*) FROM snapshot_documents WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()[0]
        groups, frequencies = {}, {}
        for row in rows:
            fid = row["feature_id"]
            if fid not in frequencies:
                frequencies[fid] = self.store.db.execute(
                    """SELECT count(DISTINCT sd.document_version_id) FROM occurrences o JOIN snapshot_occurrences so ON so.occurrence_id=o.id
                    JOIN segments s ON s.id=o.segment_id JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id AND sd.snapshot_id=so.snapshot_id WHERE so.snapshot_id=? AND o.feature_id=?""",
                    (snapshot_id, fid),
                ).fetchone()[0]
            tier = (
                1
                if row["kind"] in ("identifier", "original_blob", "message_id", "document_reference")
                else 2
                if row["kind"] == "paragraph"
                else 3
            )
            item = groups.setdefault(
                row["document_version_id"],
                {"id": row["document_version_id"], "tier": tier, "idf_rank": 0, "reasons": []},
            )
            item["tier"] = min(tier, item["tier"])
            item["idf_rank"] += math.log1p(total / frequencies[fid])
            item["reasons"].append(
                {"feature_id": fid, "kind": row["kind"], "document_frequency": frequencies[fid]}
            )
        for link in self.store.rows(
            """SELECT l.* FROM snapshot_links sl JOIN explicit_links l ON l.id=sl.link_id WHERE sl.snapshot_id=? AND (l.from_node=? OR l.to_node=?)""",
            (snapshot_id, document_version_id, document_version_id),
        ):
            other = link["to_node"] if link["from_node"] == document_version_id else link["from_node"]
            if not other:
                continue
            item = groups.setdefault(other, {"id": other, "tier": 0, "idf_rank": 0, "reasons": []})
            item["tier"] = 0
            item["reasons"].append(
                {"link_id": link["id"], "relationship": link["relation_type"], "status": link["status"]}
            )
        for item in groups.values():
            item["order_key"] = [item["tier"], -item["idf_rank"], item["id"]]
            item["reason_count"] = len(item["reasons"])
            item["reasons"] = item["reasons"][:8]
            item["remaining_reasons"] = max(0, item["reason_count"] - 8)
            item["evidence_tool"] = {
                "tool": "explain_connection",
                "left_document_id": document_version_id,
                "right_document_id": item["id"],
                "snapshot_id": snapshot_id,
            }
        return self.page(
            snapshot_id,
            {
                "kind": "ranked_related_preview",
                "document": document_version_id,
                "score_meaning": "ranking only, never relationship probability",
            },
            list(groups.values()),
            cursor,
            limit,
            key=lambda r: r["order_key"],
        )

    def document_details(self, snapshot_id, document_version_id, kind="warnings", cursor=None, limit=100):
        if kind not in ("warnings", "artifacts"):
            raise ValueError("kind must be warnings or artifacts")
        matches = [
            d
            for d in self.store.manifest(snapshot_id)["documents"]
            if d["document_version_id"] == document_version_id
        ]
        if not matches:
            raise ValueError("Unknown snapshot document")
        rows = [{"id": f"{i:016d}", "value": v} for i, v in enumerate(matches[0].get(kind, []))]
        return self.page(
            snapshot_id,
            {"kind": "document_details", "document": document_version_id, "field": kind},
            rows,
            cursor,
            limit,
        )
