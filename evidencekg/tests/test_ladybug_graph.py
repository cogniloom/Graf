"""Native LadybugDB persistence, publication, identity and concurrent-reader checks."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evidencekg.db import dump
from evidencekg.experiments import _read
from evidencekg.hybrid import graph
from evidencekg.hybrid.graph import LadybugGraph, prepare_graph
from evidencekg.hybrid.sources import load_sources
from evidencekg.ingest import ingest


def edge(key="edge", source="a", target="b", status="resolved"):
    return dict(
        id=key,
        from_node=source,
        to_node=target,
        status=status,
        relation_type="REFERENCE",
        rule_id="literal",
        rule_version="1",
        derivation_json=dump({"locator": "line 1", "text": 'quoted, "é"\nnext line'}),
    )


def test_roundtrip_reuse_and_two_reader_processes(tmp_path):
    links = [
        edge(),
        edge("loop", "a", "a"),
        edge("back", "b", "a"),
        edge("unknown", "a", None, "unresolved"),
        edge("outside", "a", "missing"),
    ]
    config = prepare_graph(tmp_path, "snapshot", "f" * 64, ["a", "b"], links)
    before = Path(config["path"]).stat().st_mtime_ns
    assert prepare_graph(tmp_path, "snapshot", "f" * 64, ["b", "a"], links) == config
    assert Path(config["path"]).stat().st_mtime_ns == before
    assert config["edges"] == 3 and config["remaining_links"] == 2
    with LadybugGraph(config) as first, LadybugGraph(config) as second:
        assert first.neighbors("a") == links[:3]
        assert second.neighbors("b") == [links[0], links[2]]
        assert second.neighbors("absent") == []
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(first.neighbors, ["a"] * 8)) == [links[:3]] * 8
        program = """import json,sys
from evidencekg.hybrid.graph import LadybugGraph
with LadybugGraph(json.loads(sys.argv[1])) as graph:
    print(json.dumps(graph.neighbors('a')))
"""
        proc = subprocess.run(
            [sys.executable, "-c", program, json.dumps(config)], capture_output=True, text=True, check=True
        )
        assert json.loads(proc.stdout) == links[:3]
    first.close()
    with pytest.raises(ValueError, match="closed"):
        first.neighbors("a")


def test_empty_graph_and_quoted_identifiers(tmp_path):
    empty = prepare_graph(tmp_path, "empty", "f" * 64, [], [])
    with LadybugGraph(empty) as reader:
        assert reader.neighbors("absent") == []
    unusual = "x'\"\\\n💡"
    links = [edge(source=unusual)]
    config = prepare_graph(tmp_path / "quoted'\"", "quoted", "f" * 64, [unusual, "b"], links)
    with LadybugGraph(config) as reader:
        assert reader.neighbors(unusual) == links


def test_corruption_and_metadata_rejected(tmp_path):
    config = prepare_graph(tmp_path, "snapshot", "f" * 64, ["a", "b"], [edge()])
    with pytest.raises(ValueError, match="configuration drift"):
        LadybugGraph(config | {"snapshot_id": "different"})
    path = Path(config["path"])
    with path.open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="content drift"):
        LadybugGraph(config)
    with pytest.raises(ValueError, match="content drift"):
        prepare_graph(tmp_path, "snapshot", "f" * 64, ["a", "b"], [edge()])


def test_invalid_input_and_failed_copy_never_publish(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="unique"):
        prepare_graph(tmp_path, "duplicate", "f" * 64, ["a", "b"], [edge(), edge()])
    good = prepare_graph(tmp_path, "good", "f" * 64, ["a", "b"], [edge()])
    original = graph._execute

    def broken(connection, query, parameters=None):
        if query.startswith("COPY ExplicitLink"):
            raise RuntimeError("injected disk failure")
        return original(connection, query, parameters)

    monkeypatch.setattr(graph, "_execute", broken)
    with pytest.raises(RuntimeError, match="disk failure"):
        prepare_graph(tmp_path, "new", "f" * 64, ["a", "b"], [edge()])
    assert [p.name for p in tmp_path.iterdir() if p.is_dir()] == [Path(good["path"]).parent.name]
    assert _read(Path(good["path"]).parent / "manifest.json") == good
    with LadybugGraph(good) as reader:
        assert reader.neighbors("a") == [edge()]


def test_new_generation_does_not_change_open_reader(tmp_path):
    old = prepare_graph(tmp_path, "snapshot", "f" * 64, ["a", "b"], [edge()])
    with LadybugGraph(old) as reader:
        new = prepare_graph(tmp_path, "snapshot2", "f" * 64, ["a", "b"], [edge("new")])
        assert reader.neighbors("a") == [edge()]
        with LadybugGraph(new) as next_reader:
            assert next_reader.neighbors("a") == [edge("new")]


def test_real_ingestion_provenance_survives_native_graph(vault, tmp_path):
    root, store = vault
    (root / "a.md").write_text("Original approval. [[b.md]] [[missing.md]]")
    (root / "b.md").write_text("Original invoice. [[a.md]]")
    snapshot = ingest(store)
    _, segments, links = load_sources(store, snapshot)
    docs = {s["document_version_id"] for s in segments.values()}
    assert any(link["status"] == "resolved" for link in links)
    assert any(link["status"] == "unresolved" for link in links)
    config = prepare_graph(
        tmp_path / "graphs", snapshot, store.snapshot(snapshot)["manifest_sha"], docs, links
    )
    with LadybugGraph(config) as reader:
        for doc in docs:
            expected = [
                link
                for link in links
                if link["status"] == "resolved"
                and link["from_node"] in docs
                and link["to_node"] in docs
                and doc in (link["from_node"], link["to_node"])
            ]
            assert reader.neighbors(doc) == expected
