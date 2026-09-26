"""Append-only PostgreSQL worksets, verified snapshot copies, and retrieval cache.

The importer consumes the source-verified payload from ``load_sources``; it does
not read or certify source files itself. Hashes bind that payload and all of its
provenance on import and readback. Only ``migrate`` executes DDL.
"""

from __future__ import annotations

import base64
import json
import threading
from contextlib import contextmanager
from pathlib import Path

from evidencekg.db import dump, ident, sha


def _connect(dsn):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)


def migrate(dsn):
    """Create schema explicitly using a migration-owner DSN, atomically."""
    with _connect(dsn) as db, db.transaction():
        db.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_id("hybrid-schema-v1"),))
        db.execute(Path(__file__).with_name("schema.sql").read_text())


def _lock_id(value):
    return int.from_bytes(bytes.fromhex(sha(value))[:8], "big", signed=True)


def _snapshot_payload(manifest_sha, manifest, segments, links):
    """Validate source bindings without normalizing text or original locators."""
    base = dict(manifest)
    if "hybrid_manifest_sha" in base:
        if base.pop("hybrid_manifest_sha") != manifest_sha:
            raise ValueError("Snapshot manifest provenance drift")
        base.pop("hybrid_typed_postings", None)
    if sha(dump(base)) != manifest_sha:
        raise ValueError("Snapshot manifest hash mismatch")
    documents = manifest["documents"]
    docs = {d["document_version_id"]: d for d in documents}
    if len(docs) != len(documents):
        raise ValueError("Duplicate snapshot document")
    grouped = {key: [] for key in docs}
    for sid, segment in segments.items():
        doc = docs.get(segment["document_version_id"])
        if (
            doc is None
            or sid != segment["id"]
            or segment["extraction_id"] != doc["extraction_id"]
            or segment["source_path"] != doc["path"]
            or sha(segment["text"]) != segment["text_sha"]
            or segment["char_end"] - segment["char_start"] != len(segment["text"])
            or segment["char_start"] < 0
            or type(segment["ordinal"]) is not int
            or segment["ordinal"] < 0
            or sid
            != ident(
                "S", segment["extraction_id"], segment["ordinal"], segment["char_start"], segment["char_end"]
            )
            or not isinstance(segment["locators"], list)
            or not isinstance(segment["artifact_sha"], str)
            or len(segment["artifact_sha"]) != 64
        ):
            raise ValueError("Snapshot passage source integrity mismatch")
        grouped[segment["document_version_id"]].append(segment)
    for parts in grouped.values():
        if not parts:
            raise ValueError("Snapshot document passage inventory gap")
        position = 0
        for ordinal, part in enumerate(sorted(parts, key=lambda s: s["ordinal"])):
            if part["ordinal"] != ordinal or part["char_start"] != position:
                raise ValueError("Snapshot passage coverage gap or overlap")
            position = part["char_end"]
    if len({edge["id"] for edge in links}) != len(links):
        raise ValueError("Duplicate snapshot edge")
    for edge in links:
        if not edge["from_node"] or not edge["relation_type"] or not edge["status"]:
            raise ValueError("Snapshot edge provenance missing")
        if not edge["rule_id"] or not edge["rule_version"] or not edge["derivation_json"]:
            raise ValueError("Snapshot edge provenance missing")
    return dump({"manifest": manifest, "segments": segments, "links": links})


class PostgresWorkset:
    """One connection; threads serialize and processes coordinate with xact locks."""

    def __init__(self, dsn):
        self.db = _connect(dsn)
        self._mutex = threading.RLock()

    @contextmanager
    def _locked(self, key):
        with self._mutex, self.db.transaction():
            self.db.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_id(key),))
            yield

    @contextmanager
    def query_lock(self, identity):
        """Serialize a complete get/compute/freeze/put sequence across processes.

        Nested methods use savepoints. An exception rolls back the complete
        sequence, and commit/rollback or connection loss releases the lock.
        """
        with self._locked(sha(dump(identity))):
            yield self

    def _inventory(self, key):
        run = self.db.execute("SELECT * FROM public.hybrid_runs WHERE id=%s", (key,)).fetchone()
        if run is None:
            raise ValueError("Unknown workset")
        stored = self.db.execute(
            "SELECT c.*, r.receipt, r.receipt_sha FROM public.hybrid_candidates c "
            "LEFT JOIN public.hybrid_reviews r ON (c.run=r.run AND c.sid=r.sid) "
            "WHERE c.run=%s ORDER BY c.position",
            (key,),
        ).fetchall()
        rows = [json.loads(r["payload"]) for r in stored]
        manifest = json.loads(run["manifest"])
        if (
            sha(run["identity"]) != key
            or sha(run["manifest"]) != run["manifest_sha"]
            or manifest["count"] != len(rows)
            or manifest["rows_sha"] != sha(dump(rows))
            or any(r["position"] != i or r["sid"] != rows[i]["segment_id"] for i, r in enumerate(stored))
            or any(r["receipt"] is not None and sha(r["receipt"]) != r["receipt_sha"] for r in stored)
        ):
            raise ValueError("Candidate inventory drift")
        return run, manifest, stored

    def freeze(self, identity, rows, accounting):
        rows = list(rows)
        if len({r["segment_id"] for r in rows}) != len(rows):
            raise ValueError("Duplicate candidate identity")
        encoded = dump(identity)
        key = sha(encoded)
        manifest = dump({"rows_sha": sha(dump(rows)), "accounting": accounting, "count": len(rows)})
        with self._locked(key):
            existing = self.db.execute("SELECT id FROM public.hybrid_runs WHERE id=%s", (key,)).fetchone()
            if existing:
                run, _, _ = self._inventory(key)
                if run["identity"] != encoded or run["manifest"] != manifest:
                    raise ValueError("Frozen workset drift")
            else:
                self.db.execute(
                    "INSERT INTO public.hybrid_runs VALUES (%s,%s,%s,%s)",
                    (key, encoded, manifest, sha(manifest)),
                )
                with self.db.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO public.hybrid_candidates VALUES (%s,%s,%s,%s)",
                        [(key, r["segment_id"], i, dump(r)) for i, r in enumerate(rows)],
                    )
        return key

    def page(self, key, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid page size")
        with self._mutex, self.db.transaction():
            run, manifest, stored = self._inventory(key)
        scope = sha(run["identity"] + run["manifest"])
        start = -1
        if cursor is not None:
            try:
                value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                start = value["position"]
                if value["scope"] != scope or type(start) is not int or not -1 <= start < len(stored):
                    raise ValueError("Foreign cursor")
            except Exception as exc:
                raise ValueError("Invalid or foreign cursor") from exc
        selected = stored[start + 1 : start + 1 + limit]
        nxt = None
        if start + 1 + limit < len(stored):
            nxt = base64.urlsafe_b64encode(
                dump({"scope": scope, "position": selected[-1]["position"]}).encode()
            ).decode()
        return {
            "items": [
                json.loads(r["payload"]) | {"review_state": "reviewed" if r["receipt"] else "pending"}
                for r in selected
            ],
            "next_cursor": nxt,
            "total": manifest["count"],
            "accounting": manifest["accounting"],
        }

    def mark_reviewed(self, key, sid, receipt):
        if not isinstance(receipt, dict) or not receipt:
            raise ValueError("Explicit review receipt required")
        encoded = dump(receipt)
        with self._locked(key):
            _, _, rows = self._inventory(key)
            row = next((row for row in rows if row["sid"] == sid), None)
            if row is None:
                raise ValueError("Candidate outside workset")
            if row["receipt"] is not None:
                if row["receipt"] != encoded:
                    raise ValueError("Review receipt drift")
            else:
                self.db.execute(
                    "INSERT INTO public.hybrid_reviews VALUES (%s,%s,%s,%s)",
                    (key, sid, encoded, sha(encoded)),
                )

    def import_snapshot(self, snapshot_id, manifest_sha, manifest, segments, links):
        """Atomically retain an already source-verified snapshot; retries must match."""
        if not isinstance(segments, dict):
            source_rows = list(segments)
            segments = {s["id"]: s for s in source_rows}
            if len(segments) != len(source_rows):
                raise ValueError("Duplicate snapshot passage")
        payload = _snapshot_payload(manifest_sha, manifest, segments, links)
        with self._locked("snapshot:" + snapshot_id):
            existing = self.db.execute(
                "SELECT * FROM public.hybrid_snapshots WHERE id=%s", (snapshot_id,)
            ).fetchone()
            if existing:
                retained = self.load_snapshot(snapshot_id)
                if existing["manifest_sha"] != manifest_sha or retained != (manifest, segments, links):
                    raise ValueError("Immutable snapshot drift")
                return snapshot_id
            self.db.execute(
                "INSERT INTO public.hybrid_snapshots VALUES (%s,%s,%s,%s,%s,%s)",
                (snapshot_id, manifest_sha, dump(manifest), sha(payload), len(segments), len(links)),
            )
            with self.db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO public.hybrid_documents VALUES (%s,%s,%s,%s)",
                    [
                        (snapshot_id, d["document_version_id"], i, dump(d))
                        for i, d in enumerate(manifest["documents"])
                    ],
                )
                cursor.executemany(
                    "INSERT INTO public.hybrid_passages VALUES (%s,%s,%s,%s,%s)",
                    [
                        (snapshot_id, sid, s["document_version_id"], s["ordinal"], dump(s))
                        for sid, s in segments.items()
                    ],
                )
                cursor.executemany(
                    "INSERT INTO public.hybrid_edges VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (snapshot_id, e["id"], i, e["from_node"], e["to_node"], e["relation_type"], dump(e))
                        for i, e in enumerate(links)
                    ],
                )
        return snapshot_id

    def load_snapshot(self, snapshot_id):
        with self._mutex, self.db.transaction():
            row = self.db.execute(
                "SELECT * FROM public.hybrid_snapshots WHERE id=%s", (snapshot_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Unknown snapshot")
            docs = self.db.execute(
                "SELECT * FROM public.hybrid_documents WHERE snapshot=%s ORDER BY position", (snapshot_id,)
            ).fetchall()
            passages = self.db.execute(
                "SELECT * FROM public.hybrid_passages WHERE snapshot=%s ORDER BY id", (snapshot_id,)
            ).fetchall()
            edges = self.db.execute(
                "SELECT * FROM public.hybrid_edges WHERE snapshot=%s ORDER BY position", (snapshot_id,)
            ).fetchall()
            manifest = json.loads(row["manifest"])
            segments = {s["id"]: json.loads(s["payload"]) for s in passages}
            links = [json.loads(e["payload"]) for e in edges]
            payload = _snapshot_payload(row["manifest_sha"], manifest, segments, links)
            if (
                sha(payload) != row["content_sha"]
                or len(passages) != row["passage_count"]
                or len(edges) != row["edge_count"]
                or [json.loads(d["payload"]) for d in docs] != manifest["documents"]
                or any(
                    d["position"] != i or d["id"] != manifest["documents"][i]["document_version_id"]
                    for i, d in enumerate(docs)
                )
                or any(
                    s["document_id"] != segments[s["id"]]["document_version_id"]
                    or s["ordinal"] != segments[s["id"]]["ordinal"]
                    for s in passages
                )
                or any(
                    e["position"] != i
                    or any(e[k] != links[i][k] for k in ("id", "from_node", "to_node", "relation_type"))
                    for i, e in enumerate(edges)
                )
            ):
                raise ValueError("Snapshot content integrity mismatch")
            return manifest, segments, links

    def get_result(self, identity):
        encoded = dump(identity)
        key = sha(encoded)
        with self._mutex, self.db.transaction():
            row = self.db.execute("SELECT * FROM public.hybrid_results WHERE id=%s", (key,)).fetchone()
            if row is None:
                return None
            if row["identity"] != encoded or sha(row["payload"]) != row["payload_sha"]:
                raise ValueError("Retrieval cache integrity mismatch")
            return json.loads(row["payload"])

    def put_result(self, identity, result):
        if result is None:
            raise ValueError("A retrieval result is required")
        encoded, payload = dump(identity), dump(result)
        key = sha(encoded)
        with self._locked(key):
            existing = self.get_result(identity)
            if existing is not None:
                if dump(existing) != payload:
                    raise ValueError("Immutable retrieval result drift")
            else:
                self.db.execute(
                    "INSERT INTO public.hybrid_results VALUES (%s,%s,%s,%s)",
                    (key, encoded, payload, sha(payload)),
                )
        return key

    def close(self):
        with self._mutex:
            self.db.close()
