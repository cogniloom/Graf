"""Whole-graph storage, bounded reads and transaction failure regressions."""

import json
import os
import subprocess
import sys

import pytest
from test_knowledge import vault

from evidencekg import knowledge
from evidencekg import knowledge_storage as storage
from evidencekg.db import dump, sha
from evidencekg.ingest import ingest

__all__ = ["vault"]


class Blobs:
    def __init__(self):
        self.data = {}

    def put(self, raw):
        digest = sha(raw)
        self.data[digest] = raw
        return digest

    def get(self, digest):
        raw = self.data[digest]
        if sha(raw) != digest:
            raise ValueError("Corrupt artifact")
        return raw


def test_shards_are_lazy_replayable_and_preserve_every_row(monkeypatch):
    store = Blobs()
    monkeypatch.setattr(storage, "GRAPH_CHUNK_BYTES", 1000)
    monkeypatch.setattr(storage, "MAX_DECODED_BYTES", 16000)
    graph = dict(nodes=[dict(id=str(i), quote="ä🙂" * 300) for i in range(100)], edges=[], coverage={})
    assert len(dump(graph).encode()) > storage.MAX_DECODED_BYTES
    digest = storage.put_graph(store, graph)
    value = storage.read(store, digest)
    assert isinstance(value["nodes"], storage.ShardedRows)
    assert value == graph
    assert list(value["nodes"]) == graph["nodes"]  # A second pass is complete.
    assert value["nodes"][-1] == graph["nodes"][-1]
    assert value["nodes"][2:5] == graph["nodes"][2:5]
    assert value["nodes"][8:1:-2] == graph["nodes"][8:1:-2]
    assert value["nodes"] != graph["nodes"][:-1]
    with pytest.raises(IndexError):
        value["nodes"][len(value["nodes"])]


@pytest.mark.parametrize("damage", ["hash", "missing", "count", "nested", "truncated", "trailing"])
def test_invalid_shards_fail_closed(damage):
    store = Blobs()
    digest = storage.put_graph(store, dict(nodes=[dict(id="one")], edges=[]))
    manifest = json.loads(__import__("zlib").decompress(store.get(digest)[len(storage.MAGIC) :]))
    chunk = manifest["nodes"][0]
    if damage == "hash":
        store.data[chunk["blob"]] = b"corrupt"
    elif damage == "missing":
        del store.data[chunk["blob"]]
    elif damage == "count":
        chunk["count"] += 1
    elif damage == "nested":
        chunk["blob"] = digest
    else:
        raw = store.get(chunk["blob"])
        chunk["blob"] = store.put(raw[:-2] if damage == "truncated" else raw + b"extra")
    digest = storage.put(store, manifest)
    with pytest.raises((ValueError, KeyError)):
        list(storage.read(store, digest)["nodes"])


def test_disk_graph_matches_in_memory_graph_and_chunk_failure_rolls_back(vault, monkeypatch):
    root, store = vault
    (root / "a.txt").write_text("Order 12 was approved by Alice on 1 March 2026.\n" * 20)
    (root / "b.txt").write_text("Order 12 was rejected by Alice on 1 March 2026.\n" * 20)
    first = ingest(store)
    manifest = store.manifest(first)
    expected = knowledge.graph_for(
        manifest["documents"], lambda doc: storage.read(store, doc["knowledge"]["blob"])
    )
    actual = knowledge.load(store, first)
    assert all(actual[key] == expected[key] for key in ("nodes", "edges", "graph_gaps"))
    knowledge.verify(store, first)
    (root / "c.txt").write_text("Order 13 was approved.")
    original = store.put

    def fail_chunk(raw):
        if isinstance(raw, bytes) and raw.startswith(storage.MAGIC):
            import zlib

            if zlib.decompress(raw[len(storage.MAGIC) :]).startswith(b"["):
                raise OSError("test disk full")
        return original(raw)

    monkeypatch.setattr(store, "put", fail_chunk)
    with pytest.raises(OSError, match="test disk full"):
        ingest(store)
    assert store.one("SELECT snapshot_id FROM current_snapshot")["snapshot_id"] == first
    assert len(store.rows("SELECT * FROM snapshots")) == 1


@pytest.mark.skipif(sys.platform != "linux", reason="Linux address-space regression")
def test_full_graph_write_and_read_under_memory_cap(tmp_path):
    program = r"""
import json, resource, sys
from pathlib import Path
from evidencekg import knowledge, knowledge_storage
from evidencekg.config import initialize
from evidencekg.knowledge_graph import Graph
resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, resource.getrlimit(resource.RLIMIT_AS)[1]))
root = Path(sys.argv[1]); (root / "sources").mkdir()
store = initialize(root / "vault", root / "sources", {"ocr": "off"})
documents = [dict(document_version_id=str(i), extraction_id=str(i), path=str(i)) for i in range(5000)]
def artifact(doc):
    text = "🙂 " + " ".join(f"word{j}" for j in range(450))
    return dict(claims=[], observations=[dict(id="o"+doc["extraction_id"], kind="observation",
        category="dependence_passage", document_version_id=doc["document_version_id"],
        quote=text, concepts=[], normalization_status="surface_only", sources=[dict(quote=text)])])
with store.write(), Graph() as graph_store:
    graph = knowledge.graph_for(documents, artifact, graph_store)
    digest = knowledge_storage.put_graph(store, graph)
value = knowledge_storage.read(store, digest)
assert sum(1 for n in value["nodes"] if n["kind"] == "observation") == 5000
assert sum(1 for e in value["edges"]) == 5000
assert value["graph_gaps"] == {"dependence_common_fingerprint_skipped": 32}
# ru_maxrss may include the pre-exec fork parent's high-water mark. VmPeak
# describes this executed child and is directly comparable to RLIMIT_AS.
peak = next(line.split()[1] for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("VmPeak:"))
print(json.dumps({"peak_address_space_kib": int(peak)}))
store.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["peak_address_space_kib"] < 256 * 1024


def test_in_memory_enrichment_can_add_supersession_entities():
    from evidencekg import knowledge_links

    nodes = {
        "c": dict(
            id="c", kind="claim", document_version_id="d", object_key="policy:P1", predicate="supersession"
        )
    }
    edges = []
    knowledge_links.enrich(nodes, edges)
    assert any(n.get("key") == "policy:P1" for n in nodes.values())
    assert [edge["relation"] for edge in edges] == ["STATES_SUPERSESSION_OF"]


def test_reusing_graph_checks_child_chunks(vault):
    root, store = vault
    (root / "a.txt").write_text("Order 12 was approved.")
    sid = ingest(store)
    manifest = store.manifest(sid)
    graph = knowledge.load(store, sid)
    digest = graph["nodes"].chunks[0]["blob"]
    (store.state / "objects" / digest[:2] / digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Corrupt artifact"):
        knowledge.freeze(store, manifest["documents"], manifest["knowledge"])
