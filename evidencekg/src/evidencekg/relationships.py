import json

from .db import dump, ident, sha
from .features import LIMITATIONS


def postings(store, snapshot, feature=None, document=None):
    query = """SELECT o.*,f.kind,f.namespace,f.canonical_value,f.normalization_version,
      e.document_version_id,e.id extraction_id FROM snapshot_occurrences so
      JOIN occurrences o ON o.id=so.occurrence_id JOIN features f ON f.id=o.feature_id
      JOIN segments s ON s.id=o.segment_id JOIN extractions e ON e.id=s.extraction_id
      WHERE so.snapshot_id=?"""
    args = [snapshot]
    if feature:
        query += " AND f.id=?"
        args.append(feature)
    if document:
        query += " AND e.document_version_id=?"
        args.append(document)
    return store.rows(query + " ORDER BY o.id", args)


def resolve(store, snapshot):
    legacy = store.manifest(snapshot)["configuration"]["rules_version"] == "mechanical-v1"
    docs = store.rows(
        """SELECT d.*,se.relative_path FROM snapshot_documents sd
        JOIN document_versions d ON d.id=sd.document_version_id JOIN source_entries se ON se.id=d.source_entry_id
        WHERE sd.snapshot_id=?""",
        (snapshot,),
    )
    paths = {}
    for doc in store.manifest(snapshot)["documents"]:
        paths.setdefault(doc["path"], []).append(doc["document_version_id"])
    observations = store.rows(
        """SELECT o.*,f.kind,f.canonical_value,e.document_version_id
        FROM snapshot_occurrences so JOIN occurrences o ON o.id=so.occurrence_id
        JOIN features f ON f.id=o.feature_id JOIN segments s ON s.id=o.segment_id
        JOIN extractions e ON e.id=s.extraction_id
        WHERE so.snapshot_id=? AND f.kind IN ('message_id','reply_reference','document_reference')""",
        (snapshot,),
    )
    targets = {}
    for obs in observations:
        if obs["kind"] == "message_id":
            targets.setdefault(obs["canonical_value"], set()).add(obs["document_version_id"])

    def link(source, relation, target, status, derivation, evidence=()):
        spans = sorted((r["segment_id"], r["start"], r["end"]) for r in evidence)
        lid = (
            ident("L", source, relation, target, status, derivation)
            if legacy
            else ident("L", source, relation, target, status, derivation, spans)
        )
        store.db.execute(
            "INSERT OR IGNORE INTO explicit_links VALUES(?,?,?,?,?,?,?,?)",
            (
                lid,
                source,
                relation,
                target,
                status,
                dump(derivation),
                relation.lower(),
                "mechanical-v1" if legacy else "mechanical-link-v2",
            ),
        )
        for ref in evidence:
            store.db.execute(
                "INSERT OR IGNORE INTO link_evidence VALUES(?,?,?,?)",
                (lid, ref["segment_id"], ref["start"], ref["end"]),
            )
        store.db.execute("INSERT OR IGNORE INTO snapshot_links VALUES(?,?)", (snapshot, lid))

    for doc in docs:
        if doc["parent_document_version_id"]:
            link(
                doc["id"],
                "ATTACHMENT_OF",
                doc["parent_document_version_id"],
                "resolved",
                {"mime_part": doc["mime_part_path"], "basis": "container_metadata"},
            )
        if doc["previous_version_id"]:
            old = store.one(
                "SELECT original_blob_sha FROM document_versions WHERE id=?", (doc["previous_version_id"],)
            )
            if (
                old["original_blob_sha"]
                and doc["original_blob_sha"]
                and old["original_blob_sha"] != doc["original_blob_sha"]
            ):
                link(
                    doc["id"],
                    "OBSERVED_VERSION_AFTER",
                    doc["previous_version_id"],
                    "resolved",
                    {"basis": "acquisition_order", "does_not_establish": ["legal supersession"]},
                )
    for obs in observations:
        if obs["kind"] == "message_id":
            continue
        candidates = (
            sorted(targets.get(obs["canonical_value"], set()))
            if obs["kind"] == "reply_reference"
            else sorted(paths.get(obs["canonical_value"], []))
        )
        status = "resolved" if len(candidates) == 1 else "ambiguous" if candidates else "unresolved"
        link(
            obs["document_version_id"],
            "EMAIL_REPLY_REFERENCE" if obs["kind"] == "reply_reference" else "EXPLICIT_DOCUMENT_REFERENCE",
            candidates[0] if status == "resolved" else None,
            status,
            {
                "observed_value": obs["canonical_value"],
                "candidates": candidates,
                "basis": "header_observation" if obs["kind"] == "reply_reference" else "exact_path_mention",
                "does_not_establish": ["sender authenticity", "truth"],
            },
            [obs],
        )


def link_sources(store, snapshot, link_id):
    """Legacy link IDs may share postings across extractions; bind reads to frozen scope."""
    return store.rows(
        """SELECT le.*,s.extraction_id FROM link_evidence le
        JOIN segments s ON s.id=le.segment_id
        JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id
        WHERE le.link_id=? AND sd.snapshot_id=? ORDER BY le.segment_id,le.start,le.end""",
        (link_id, snapshot),
    )


def reason(row):
    relations = {
        "identifier": "SHARED_IDENTIFIER",
        "paragraph": "SHARED_EXACT_PASSAGE",
        "original_blob": "SAME_CAPTURED_BYTES",
        "name_surface": "SHARED_NAME_SURFACE",
        "date": "SHARED_DATE",
    }
    return dict(
        relationship=relations.get(row["kind"], "SHARED_" + row["kind"].upper()),
        basis="captured_bytes" if row["kind"] == "original_blob" else "observed_in_extracted_text",
        feature={
            "id": row["feature_id"],
            "kind": row["kind"],
            "namespace": row["namespace"],
            "value": row["canonical_value"][:1024],
            "value_is_preview": len(row["canonical_value"]) > 1024,
            "canonical_characters": len(row["canonical_value"]),
            "canonical_sha": sha(row["canonical_value"]),
        },
        sources=[
            {
                k: row[k]
                for k in ("extraction_id", "segment_id", "start", "end", "raw_value", "document_version_id")
            }
        ],
        ambiguity=json.loads(row["ambiguity_json"]),
        rule_version=row["rule_version"],
        does_not_establish=LIMITATIONS,
    )


def explain(store, snapshot, left, right):
    for document in (left, right):
        store.one(
            "SELECT * FROM (SELECT * FROM snapshot_documents UNION ALL SELECT * FROM snapshot_history) WHERE snapshot_id=? AND document_version_id=?",
            (snapshot, document),
        )
    left_rows = postings(store, snapshot, document=left)
    right_rows = postings(store, snapshot, document=right)
    by_feature = {}
    for row in right_rows:
        by_feature.setdefault(row["feature_id"], []).append(row)
    result = []
    cache = {}
    seen = set()
    for row in left_rows:
        key = row["feature_id"]
        if key not in by_feature or key in seen:
            continue
        seen.add(key)
        found = reason(row)
        # Recheck original occurrences rather than accepting a hash hit as a quote.
        for item in [r for r in left_rows if r["feature_id"] == key] + by_feature[key]:
            verify_posting(store, item, cache)
            segment = store.one("SELECT text FROM segments WHERE id=?", (item["segment_id"],))
            if segment["text"][item["start"] : item["end"]] != item["raw_value"]:
                raise ValueError("Corrupt graph evidence")
        found["sources"] = [reason(r)["sources"][0] for r in left_rows if r["feature_id"] == key] + [
            reason(r)["sources"][0] for r in by_feature[key]
        ]
        found["source_count"] = len(found["sources"])
        if len(found["sources"]) > 32:
            found["sources"] = found["sources"][:32]
            found["remaining_sources"] = found["source_count"] - 32
            found["continuation"] = {
                "tool": "neighbours",
                "node_id": key,
                "snapshot_id": snapshot,
                "note": "Complete postings; filter document_version_id to requested endpoints",
            }
        result.append(found)
    links = store.rows(
        """SELECT l.* FROM snapshot_links sl JOIN explicit_links l ON l.id=sl.link_id
        WHERE sl.snapshot_id=? AND ((l.from_node=? AND l.to_node=?) OR (l.from_node=? AND l.to_node=?)) ORDER BY l.id""",
        (snapshot, left, right, right, left),
    )
    for link in links:
        result.insert(
            0,
            dict(
                relationship=link["relation_type"],
                from_node=link["from_node"],
                to_node=link["to_node"],
                status=link["status"],
                basis=json.loads(link["derivation_json"]),
                sources=link_sources(store, snapshot, link["id"]),
                rule_version=link["rule_version"],
            ),
        )
    return result


def verify_posting(store, row, cache=None):
    """A hash/feature hit is insufficient: recheck the captured sequence itself."""
    cache = cache if cache is not None else {}
    segment = store.one("SELECT * FROM segments WHERE id=?", (row["segment_id"],))
    if segment["text"][row["start"] : row["end"]] != row["raw_value"]:
        raise ValueError("Corrupt occurrence")
    if row["kind"] == "original_blob":
        document = store.one(
            "SELECT d.original_blob_sha FROM document_versions d JOIN extractions e ON e.document_version_id=d.id WHERE e.id=?",
            (segment["extraction_id"],),
        )
        if document["original_blob_sha"] != row["canonical_value"]:
            raise ValueError("Invalid captured-byte feature")
        if row["canonical_value"] not in cache:
            cache[row["canonical_value"]] = store.get(row["canonical_value"])
        return
    if row["kind"] == "paragraph":
        extraction = segment["extraction_id"]
        if extraction not in cache:
            artifact = store.one("SELECT artifact_sha FROM extractions WHERE id=?", (extraction,))
            cache[extraction] = json.loads(store.get(artifact["artifact_sha"]))["text"]
        bounds = json.loads(row["ambiguity_json"])
        original = cache[extraction][bounds["canonical_start"] : bounds["canonical_end"]]
        if " ".join(original.split()) != row["canonical_value"]:
            raise ValueError("Paragraph hash hit failed original sequence validation")
