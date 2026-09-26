"""Default routing, faithful lexical results and failure visibility."""

import json

from evidencekg.cli import main, parser
from evidencekg.hybrid.lexical import LexicalIndex
from evidencekg.hybrid.sources import load_sources
from evidencekg.ingest import ingest
from evidencekg.reference_ranking import _LexicalRows


def test_default_routes_to_local_hybrid(tmp_path, monkeypatch, capsys):
    from evidencekg.hybrid import runtime

    calls = []

    class FakeRuntime:
        def __init__(self, state, config):
            calls.append(("open", str(state)))

        def retrieve(self, question, limit, snapshot):
            calls.append((question, limit, snapshot))
            return {"backend": "local-hybrid-postgresql", "generative_model_calls": 0}

        def close(self):
            calls.append(("closed",))

    monkeypatch.setattr(runtime, "Runtime", FakeRuntime)
    assert parser().parse_args(["discover", "question"]).backend == "hybrid"
    assert parser().parse_args(["serve"]).backend == "hybrid"
    assert main(["--state", str(tmp_path), "discover", "question"]) == 0
    assert json.loads(capsys.readouterr().out)["generative_model_calls"] == 0
    assert calls == [("open", str(tmp_path)), ("question", 12, None), ("closed",)]


def test_unprepared_default_fails_without_fallback(tmp_path, capsys):
    assert main(["--state", str(tmp_path), "discover", "question"]) == 1
    assert "prepare-hybrid" in capsys.readouterr().err
    assert not (tmp_path / "evidence.sqlite3").exists()


def test_postgres_projection_lexical_matches_original(vault):
    root, store = vault
    for name, text in [
        ("a", "Genehmigung café 1847."),
        ("b", "Order 1847 approval approval."),
        ("c", "No approval."),
    ]:
        (root / (name + ".txt")).write_text(text)
    snapshot = ingest(store)
    _, segments, _ = load_sources(store, snapshot)
    index = LexicalIndex(segments)
    original = _LexicalRows(store)
    try:
        for q in ("1847", "approval", "cafe", "Genehmigung", "absent"):
            expected = [r["id"] for r in original.search(snapshot, q)["items"]]
            actual = [r["id"] for r in index.search(snapshot, q)["items"]]
            assert actual == expected
    finally:
        index.close()


def test_config_publication_preserves_versions(tmp_path):
    from evidencekg.experiments import _read
    from evidencekg.hybrid.runtime import publish_config

    target = tmp_path / "hybrid.json"
    publish_config(target, {"version": 1, "snapshot": "old"})
    publish_config(target, {"version": 1, "snapshot": "new"})
    publish_config(target, {"version": 1, "snapshot": "new"})
    assert _read(target)["snapshot"] == "new"
    assert len(list((tmp_path / "hybrid-configs").glob("*.json"))) == 2


def test_connection_failure_reconnects_next_request_only(monkeypatch):
    import threading

    import psycopg
    import pytest
    from evidencekg.hybrid import runtime

    calls = []

    class Broken:
        def page(self, *args, **kwargs):
            calls.append("failed")
            raise psycopg.OperationalError("connection lost")

        def close(self):
            calls.append("closed")

    class Healthy:
        def page(self, *args, **kwargs):
            return {"items": []}

    obj = object.__new__(runtime.Runtime)
    obj.config = {"database_config": "unused"}
    obj.lock = threading.RLock()
    obj.ledger = Broken()
    obj.ranker = None
    monkeypatch.setattr(runtime, "database_dsn", lambda x: "safe")
    monkeypatch.setattr(runtime, "PostgresWorkset", lambda x: Healthy())
    with pytest.raises(ValueError, match="next request"):
        obj.page("key")
    assert calls == ["failed", "closed"]
    assert obj.ledger is None
    assert obj.page("key") == {"items": []}
