"""Opt-in real local embedding/reranking, PostgreSQL and Ladybug integration."""

import os
from pathlib import Path

import pytest

from evidencekg.hybrid.postgres import migrate
from evidencekg.hybrid.runtime import Runtime, prepare
from evidencekg.ingest import ingest


def test_native_bilingual_knowledge_retrieval_and_reopen(vault, tmp_path, monkeypatch):
    dsn = os.environ.get("EVIDENCEKG_TEST_DSN")
    models = os.environ.get("EVIDENCEKG_LADYBUG_TEST_MODELS")
    if not dsn or not models:
        pytest.skip("Requires disposable EVIDENCEKG_TEST_DSN and existing local retrieval models")
    from evidencekg.hybrid import runtime

    monkeypatch.setattr(runtime, "database_dsn", lambda path: dsn)
    migrate(dsn)
    root, store = vault
    text = {
        "en.txt": "Order 1847 was approved on 12 March 2026.",
        "de.txt": "Die Bestellung 1847 wurde am 12. März 2026 nicht genehmigt.",
        "mixed.txt": "Order 1847 will be approved on 12. März 2026 only after inspection.",
    }
    for name, content in text.items():
        (root / name).write_text(content)
    snapshot = ingest(store)
    prepared = prepare(store.state, Path(models), tmp_path / "unused.json", snapshot=snapshot, device="cpu")
    serving = Runtime(store.state, prepared["config"])
    question = "Was order 1847 approved in March?"
    try:
        result = serving.retrieve(question, limit=3)
        assert result["backend"] == "local-hybrid-ladybugdb"
        assert result["generative_model_calls"] == 0
        assert not result["cache"]["hit"]
        assert {s["source_path"] for s in result["segments"]} == set(text)
        assert result["candidate_accounting"]["route_counts"]["bilingual_concept"] == 3
        assert result["candidate_accounting"]["route_counts"]["claim_context"] == 3
        claims = [claim for segment in result["segments"] for claim in segment["knowledge"]["items"]]
        assert {c["polarity"] for c in claims} == {"positive", "negative"}
        assert any(c["condition"] == "only after inspection" for c in claims)
        assert {c["language"] for c in claims} == {"en", "de"}
        retained = serving.page(result["workset_id"])
        assert retained["items"]
        german = serving.retrieve("Wurde die Bestellung 1847 im März genehmigt?", limit=3)
        assert german["generative_model_calls"] == 0
        assert {s["source_path"] for s in german["segments"]} == set(text)
        assert german["candidate_accounting"]["route_counts"]["bilingual_concept"] == 3
        assert german["workset_id"] != result["workset_id"]
    finally:
        serving.close()
    reopened = Runtime(store.state, prepared["config"])
    try:
        repeated = reopened.retrieve(question, limit=3)
        assert repeated["cache"]["hit"]
        assert repeated["workset_id"] == result["workset_id"]
        assert repeated["segments"] == result["segments"]
    finally:
        reopened.close()
