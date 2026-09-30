"""Immutable LadybugDB graph generations built from verified snapshot links.

Only prepare_graph writes. Runtime opens a closed, content-bound generation in
read-only mode so separate CLI/MCP/app processes never share a mutable writer.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import threading
from importlib.metadata import version
from pathlib import Path

from evidencekg.db import dump, sha
from evidencekg.experiments import _locked, _read, _safe, _write

SCHEMA = "graf-explicit-graph-v1"
ENGINE_VERSION = "0.21.0"
BUFFER_BYTES = 256 * 1024 * 1024
THREADS = 2


def source_identity(snapshot_id, manifest_sha, documents, links):
    """Order is significant: it controls bounded discovery's tie breaking."""
    digest = hashlib.sha256()
    for value in (SCHEMA, snapshot_id, manifest_sha, sorted(set(documents))):
        digest.update((dump(value) + "\n").encode())
    ids = set()
    for link in links:
        if not isinstance(link.get("id"), str) or not link["id"] or link["id"] in ids:
            raise ValueError("Graph requires unique nonempty link IDs")
        ids.add(link["id"])
        if not all(
            isinstance(link.get(k), str) and link[k] for k in ("from_node", "status", "relation_type")
        ):
            raise ValueError("Graph link identity missing")
        digest.update((dump(link) + "\n").encode())
    return digest.hexdigest()


def _file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _engine():
    if version("ladybug") != ENGINE_VERSION:
        raise ValueError("LadybugDB version changed; install ladybug==" + ENGINE_VERSION)
    import ladybug

    return ladybug


def _execute(connection, query, parameters=None):
    result = connection.execute(query, parameters or {})
    try:
        return _rows(result)
    finally:
        result.close()


def _rows(result):
    rows = []
    while result.has_next():
        rows.append(result.get_next())
    return rows


def _verify_import(connection, documents, links):
    """Compare native rows to input before publication, including edge endpoints."""
    result = connection.execute("MATCH (d:Document) RETURN d.id ORDER BY d.id")
    try:
        for document in documents:
            if not result.has_next() or result.get_next() != [document]:
                raise ValueError("Graph document readback mismatch")
        if result.has_next():
            raise ValueError("Graph document readback mismatch")
    finally:
        result.close()
    # One byte per link instead of a Python integer set for large imports.
    seen = bytearray(len(links))
    observed = 0
    queries = (
        "MATCH (s:Document)-[e:ExplicitLink]->(t:Document) RETURN e.position, e.payload, s.id, t.id",
        "MATCH (r:RemainingLink) RETURN r.position, r.payload",
    )
    for query in queries:
        result = connection.execute(query)
        try:
            while result.has_next():
                row = result.get_next()
                position, payload = row[:2]
                if not 0 <= position < len(links) or seen[position] or payload != dump(links[position]):
                    raise ValueError("Graph link readback mismatch")
                seen[position] = 1
                observed += 1
                if len(row) == 4 and row[2:] != [links[position]["from_node"], links[position]["to_node"]]:
                    raise ValueError("Graph endpoint readback mismatch")
        finally:
            result.close()
    if observed != len(links):
        raise ValueError("Graph link inventory readback mismatch")


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare_graph(directory, snapshot_id, manifest_sha, documents, links):
    """Bulk-load and atomically publish a new generation, or verify/reuse it."""
    directory = _safe(directory)
    documents = sorted(set(documents))
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("Graph snapshot ID required")
    if not isinstance(manifest_sha, str) or len(manifest_sha) != 64:
        raise ValueError("Graph manifest SHA-256 required")
    if any(not isinstance(doc, str) or not doc for doc in documents):
        raise ValueError("Graph document IDs must be nonempty strings")
    source_sha = source_identity(snapshot_id, manifest_sha, documents, links)
    identity = dict(
        schema=SCHEMA,
        engine_version=ENGINE_VERSION,
        snapshot_id=snapshot_id,
        manifest_sha=manifest_sha,
        source_sha=source_sha,
    )
    generation = directory / sha(dump(identity))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _locked(directory, ".prepare.lock"):
        if generation.exists():
            config = _read(generation / "manifest.json")
            if any(config.get(key) != value for key, value in identity.items()):
                raise ValueError("Graph generation identity drift")
            with LadybugGraph(config):
                pass
            return config
        engine = _engine()
        parallel = "false" if any("\n" in doc or "\r" in doc for doc in documents) else "true"
        with tempfile.TemporaryDirectory(prefix=".building-", dir=directory) as temporary:
            staging = Path(temporary)
            database = staging / "graph.lbdb"
            known = set(documents)
            edge_count = 0
            with (
                (staging / "documents.csv").open("w", newline="") as doc_file,
                (staging / "edges.csv").open("w", newline="") as edge_file,
                (staging / "remaining.csv").open("w", newline="") as remaining_file,
            ):
                docs, edges, remaining = (csv.writer(f) for f in (doc_file, edge_file, remaining_file))
                docs.writerow(["id"])
                edges.writerow(["from", "to", "position", "payload"])
                remaining.writerow(["position", "payload"])
                docs.writerows((doc,) for doc in documents)
                for position, link in enumerate(links):
                    if (
                        link["status"] == "resolved"
                        and link["from_node"] in known
                        and link.get("to_node") in known
                    ):
                        edges.writerow([link["from_node"], link["to_node"], position, dump(link)])
                        edge_count += 1
                    else:
                        remaining.writerow([position, dump(link)])
            db = engine.Database(str(database), buffer_pool_size=BUFFER_BYTES, max_num_threads=THREADS)
            connection = None
            try:
                connection = engine.Connection(db)
                _execute(connection, "CREATE NODE TABLE Document(id STRING, PRIMARY KEY(id))")
                _execute(
                    connection,
                    "CREATE REL TABLE ExplicitLink(FROM Document TO Document, position INT64, payload STRING)",
                )
                _execute(
                    connection,
                    "CREATE NODE TABLE RemainingLink(position INT64, payload STRING, PRIMARY KEY(position))",
                )
                _execute(connection, "CREATE NODE TABLE Metadata(id STRING, payload STRING, PRIMARY KEY(id))")
                for table, filename in (
                    ("Document", "documents.csv"),
                    ("ExplicitLink", "edges.csv"),
                    ("RemainingLink", "remaining.csv"),
                ):
                    # json.dumps safely quotes native Cypher string literals, including path quotes/backslashes.
                    _execute(
                        connection,
                        f"COPY {table} FROM {json.dumps(str(staging / filename))} (HEADER=true, PARALLEL={parallel})",
                    )
                _execute(
                    connection,
                    "CREATE (:Metadata {id: 'identity', payload: $payload})",
                    {"payload": dump(identity)},
                )
                counts = [
                    _execute(connection, q)[0][0]
                    for q in (
                        "MATCH (d:Document) RETURN count(d)",
                        "MATCH ()-[e:ExplicitLink]->() RETURN count(e)",
                        "MATCH (r:RemainingLink) RETURN count(r)",
                    )
                ]
                if counts != [len(documents), edge_count, len(links) - edge_count]:
                    raise ValueError("LadybugDB graph import inventory mismatch")
                _verify_import(connection, documents, links)
                _execute(connection, "CHECKPOINT")
            finally:
                if connection is not None:
                    connection.close()
                db.close()
            for filename in ("documents.csv", "edges.csv", "remaining.csv"):
                (staging / filename).unlink()
            config = dict(
                identity,
                path=str(generation / "graph.lbdb"),
                database_sha=_file_sha(database),
                documents=len(documents),
                edges=edge_count,
                remaining_links=len(links) - edge_count,
            )
            staging_config = config | {"path": str(database)}
            _write(staging / "manifest.json", staging_config)
            # Verify the closed database before exposing the generation's name.
            with LadybugGraph(staging_config):
                pass
            (staging / "manifest.json").unlink()
            _write(staging / "manifest.json", config)
            with database.open("rb") as stream:
                os.fsync(stream.fileno())
            _sync_directory(staging)
            staging.rename(generation)
            _sync_directory(directory)
        with LadybugGraph(config):
            pass
        return config


class LadybugGraph:
    """Native, bounded-resource read-only adjacency; no schema or file creation."""

    def __init__(self, config):
        self._lock = threading.RLock()
        self._db = self._connection = None
        self.config = dict(config)
        path = _safe(config["path"])
        if config.get("schema") != SCHEMA or config.get("engine_version") != ENGINE_VERSION:
            raise ValueError("Unsupported LadybugDB graph generation")
        if not path.is_file() or _read(path.parent / "manifest.json") != config:
            raise ValueError("Graph generation missing or configuration drift")
        if _file_sha(path) != config["database_sha"]:
            raise ValueError("Graph database content drift")
        engine = _engine()
        try:
            self._db = engine.Database(
                str(path), read_only=True, buffer_pool_size=BUFFER_BYTES, max_num_threads=THREADS
            )
            self._connection = engine.Connection(self._db)
            self._connection.set_query_timeout(30000)
            metadata = _execute(self._connection, "MATCH (m:Metadata {id: 'identity'}) RETURN m.payload")
            expected = {
                key: config[key]
                for key in ("schema", "engine_version", "snapshot_id", "manifest_sha", "source_sha")
            }
            if metadata != [[dump(expected)]]:
                raise ValueError("Graph snapshot metadata drift")
        except BaseException:
            self.close()
            raise

    def neighbors(self, document_id):
        # Separate directions avoid native undirected-match duplicate self-loops.
        # Position retains the source ordering, including distinct parallel edges.
        query = """MATCH (d:Document {id: $id})-[e:ExplicitLink]->() RETURN e.position, e.payload
                   UNION ALL
                   MATCH (d:Document {id: $id})<-[e:ExplicitLink]-(s:Document)
                   WHERE s.id <> d.id RETURN e.position, e.payload"""
        with self._lock:
            if self._connection is None:
                raise ValueError("LadybugDB graph is closed")
            rows = _execute(self._connection, query, {"id": document_id})
            return [json.loads(payload) for _, payload in sorted(rows)]

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._db is not None:
                self._db.close()
                self._db = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
