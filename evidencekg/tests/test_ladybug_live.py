"""Opt-in native ingestion -> PostgreSQL records + Ladybug graph -> local-model retrieval.

EVIDENCEKG_TEST_DSN must name a disposable database. Local model weights only;
no downloads, remote inference, or existing corpus reads.
"""

import json
import os
from pathlib import Path

import pytest

from evidencekg.hybrid.postgres import migrate
from evidencekg.hybrid.runtime import Runtime, prepare
from evidencekg.ingest import ingest


def test_native_hybrid_ladybug_ingest_query_reopen(vault, tmp_path, monkeypatch):
    dsn = os.environ.get("EVIDENCEKG_TEST_DSN")
    models = os.environ.get("EVIDENCEKG_LADYBUG_TEST_MODELS")
    if not dsn or not models:
        pytest.skip("Requires disposable EVIDENCEKG_TEST_DSN and EVIDENCEKG_LADYBUG_TEST_MODELS")
    from evidencekg.hybrid import runtime

    # Replace only connection-config parsing, not database/model/graph operations.
    monkeypatch.setattr(runtime, "database_dsn", lambda path: dsn)
    migrate(dsn)
    root, store = vault
    (root / "approval.md").write_text("The invoice was not approved. See [[invoice.md]].")
    (root / "invoice.md").write_text(
        "Invoice 1847 is awaiting approval. See [[approval.md]] and [[missing.md]]."
    )
    snapshot = ingest(store)
    prepared = prepare(store.state, Path(models), tmp_path / "unused.json", snapshot=snapshot, device="cpu")
    serving = Runtime(store.state, prepared["config"])
    question = "Was invoice 1847 approved?"
    try:
        result = serving.retrieve(question, limit=2)
        assert result["backend"] == "local-hybrid-ladybugdb"
        assert result["graph_backend"] == "ladybugdb"
        assert result["records_backend"] == "postgresql"
        assert not result["cache"]["hit"]
        assert result["candidate_accounting"]["route_counts"]["explicit_graph"] > 0
        assert result["candidate_accounting"]["unresolved_links"]
        assert any("not approved" in s["text"] for s in result["segments"])
        assert all(s["locators"] for s in result["segments"])
        assert not serving.ranker.adjacency
        retained = serving.page(result["workset_id"])
        assert retained["items"]
        config = serving.config
    finally:
        serving.close()
    reopened = Runtime(store.state, prepared["config"])
    try:
        cached = reopened.retrieve(question, limit=2)
        assert cached["cache"]["hit"]
        assert cached["source_context"] == result["source_context"]
        assert cached["workset_id"] == result["workset_id"]
        assert reopened.graph is not None  # validates graph even on cache hits
    finally:
        reopened.close()
    # A retained query must not hide a missing graph on restart.
    graph_path = Path(config["graph"]["path"])
    graph_path.rename(graph_path.with_suffix(".held"))
    broken = Runtime(store.state, prepared["config"])
    try:
        with pytest.raises(ValueError, match="missing"):
            broken.retrieve(question, limit=2)
    finally:
        broken.close()
        graph_path.with_suffix(".held").rename(graph_path)
    report = os.environ.get("EVIDENCEKG_LADYBUG_TEST_REPORT")
    if report:
        Path(report).write_text(
            json.dumps(
                {
                    "backend": result["backend"],
                    "graph": config["graph"],
                    "source_context": result["source_context"],
                    "candidate_accounting": result["candidate_accounting"],
                    "cache_reopen_verified": True,
                    "missing_graph_cache_rejected": True,
                    "native_models": config["dependencies"],
                    "generative_calls": 0,
                },
                indent=2,
            )
        )
