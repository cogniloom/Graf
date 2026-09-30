"""Real vault/graph/query integration; ranking doubles isolate candidate routing."""

from types import SimpleNamespace

import pytest
from test_knowledge import vault, write_claims

from evidencekg import knowledge
from evidencekg.app.manager import Manager
from evidencekg.hybrid.retrieval import HybridDiscovery, Policy
from evidencekg.hybrid.sources import load_sources
from evidencekg.ingest import ingest
from evidencekg.retrieval import API

__all__ = ["vault"]


def test_bilingual_evidence_set_retains_qualifiers_other_and_unknown_days(vault):
    root, store = vault
    write_claims(root)
    (root / "de.txt").write_text("Die Bestellung 1847 wurde am 12. Mai 2026 nicht genehmigt.")
    sid = ingest(store)
    api = API(store)
    page = api.knowledge_query(
        sid, kind="evidence_set", entity="Bestellung:1847", applicable_on="May 12, 2026", limit=1
    )
    assert page["total"] == 6
    assert page["evidence_set"]["roles"]["qualified"] == 1
    assert page["evidence_set"]["roles"]["time_unknown_or_ambiguous"] == 1
    assert page["evidence_set"]["roles"]["other_event_day"] == 1
    rows = page["items"][:]
    while page["next_cursor"]:
        page = api.knowledge_query(
            sid,
            kind="evidence_set",
            entity="Bestellung:1847",
            applicable_on="May 12, 2026",
            limit=1,
            cursor=page["next_cursor"],
        )
        rows.extend(page["items"])
    assert len({r["id"] for r in rows}) == 6
    assert {r["language"] for r in rows} == {"en", "de"}
    assert any(r["condition"] == "only after inspection" for r in rows)
    assert any(r["related_groups"] for r in rows)
    assert {r["event_day_relation"] for r in rows} == {"same", "after", "unknown"}
    with pytest.raises(ValueError, match="Evidence sets"):
        api.knowledge_query(sid, kind="evidence_set", entity="order:1847", text="approved")


def test_frozen_concepts_cursor_binding_and_exact_segment_continuation(vault, monkeypatch):
    root, store = vault
    (root / "mixed.txt").write_text(
        "Order 1847 was approved on 12 March 2026.\nDie Bestellung 1848 wurde am 13. März 2026 genehmigt."
    )
    sid = ingest(store)
    api = API(store)
    first = api.knowledge_query(sid, kind="claim", concept="month:3", limit=1)
    assert first["total"] == 2
    assert {c["id"] for c in api.knowledge_query(sid, kind="observation", text="March")["items"]} == {
        c["id"] for c in api.knowledge_query(sid, kind="observation", text="März")["items"]
    }
    _, segments, _ = load_sources(store, sid)
    for segment in segments.values():
        metadata = segment["knowledge"]
        continued = api.knowledge_query(sid, segment_id=segment["id"])
        assert continued["total"] == metadata["total"]
        assert all(any(r["segment_id"] == segment["id"] for r in c["sources"]) for c in continued["items"])
    # A changed query normalizer may expand new queries, but cannot reinterpret
    # the concepts stored on old claims or reuse an old continuation receipt.
    previous = knowledge.signature()
    monkeypatch.setattr(
        knowledge,
        "signature",
        lambda: dict(previous, modules=previous["modules"] | {"knowledge_language.py": "changed"}),
    )
    monkeypatch.setattr(knowledge, "query_concepts", lambda text: ["month:7"])
    assert api.knowledge_query(sid, concept="month:3")["total"] == 2
    assert api.knowledge_query(sid, concept="month:7")["total"] == 0
    with pytest.raises(ValueError, match="cursor"):
        api.knowledge_query(sid, concept="month:3", cursor=first["next_cursor"], limit=1)


class EmptyLexical:
    def search(self, *args):
        return {"items": []}


class Ranking:
    def score(self, question, texts):
        return SimpleNamespace(scores=[1.0] * len(texts), windows=[[0]] * len(texts))


class Worksets:
    def freeze(self, identity, rows, accounting):
        self.rows, self.accounting = rows, accounting
        return "fixture-retained-workset"


def test_concept_candidate_route_crosses_languages_and_discloses_cap(vault):
    root, store = vault
    (root / "german.txt").write_text("Die Rechnung INV-12 wurde am 12. März 2026 bezahlt.")
    (root / "english.txt").write_text("Invoice INV-13 was paid on March 13, 2026.")
    (root / "irrelevant.txt").write_text("Only January is mentioned here.")
    sid = ingest(store)
    manifest, segments, links = load_sources(store, sid)
    manifest["hybrid_manifest_sha"] = store.snapshot(sid)["manifest_sha"]
    ledger = Worksets()
    ranker = HybridDiscovery(
        None,
        sid,
        lambda q: [],
        Ranking(),
        ledger,
        sources=(manifest, segments, links),
        typed=[],
        lexical=EmptyLexical(),
        model_identity={},
        policy=Policy(knowledge_candidates=1, graph_depth=0),
    )
    result = ranker.retrieve("March", limit=1)
    assert result["knowledge_retrieval"]["matched_segments"] == 2
    assert result["knowledge_retrieval"]["omitted_candidates"] == 1
    assert result["candidate_accounting"]["route_counts"]["bilingual_concept"] == 1
    assert any(reason["route"] == "bilingual_concept" for row in ledger.rows for reason in row["routes"])
    assert result["segments"][0]["knowledge"]["epistemic_status"].startswith("unreviewed")


def test_graph_projection_and_published_manager_read_use_real_vault(vault):
    root, store = vault
    source = root / "src"
    source.mkdir()
    (source / "de.txt").write_text("Die Bestellung 1847 wurde am 12. März 2026 genehmigt.")
    sid = ingest(store)
    manager = Manager.__new__(Manager)
    row = {
        "snapshot_id": sid,
        "publication": {
            "state": str(store.state),
            "sources": [{"id": "src", "kind": "directory", "path": str(source)}],
        },
    }
    manager.evidence = lambda operation: operation(row)  # isolate the existing publication fence
    manager._snapshot = lambda row: load_sources(store, sid)
    result = manager.knowledge(entity="order:1847")
    assert result["items"][0]["source_path"] == str(source / "de.txt")
    graph = manager.graph(detailed=True)
    ids = {n["id"] for n in graph["nodes"]}
    assert any(n["kind"] == "knowledge_claim" for n in graph["nodes"])
    assert any(n["kind"] == "knowledge_concept" and n["label"] == "month:3" for n in graph["nodes"])
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    assert any(e["type"] == "LEXICAL_MAPPING" for e in graph["edges"])
    assert all(not n["kind"].startswith("knowledge") for n in manager.graph()["nodes"])


def test_claim_context_bundle_keeps_contrary_and_qualified_evidence(vault):
    root, store = vault
    texts = {
        "approval.txt": "Order 1847 was approved on 12 March 2026.",
        "denial.txt": "Die Bestellung 1847 wurde am 12. März 2026 nicht genehmigt.",
        "condition.txt": "Order 1847 will be approved only after inspection.",
    }
    for name, text in texts.items():
        (root / name).write_text(text)
    sid = ingest(store)
    manifest, segments, links = load_sources(store, sid)
    manifest["hybrid_manifest_sha"] = store.snapshot(sid)["manifest_sha"]
    seed = next(s["id"] for s in segments.values() if s["source_path"] == "approval.txt")
    ranker = HybridDiscovery(
        None,
        sid,
        lambda q: [(seed, 1.0)],
        Ranking(),
        Worksets(),
        sources=(manifest, segments, links),
        typed=[],
        lexical=EmptyLexical(),
        model_identity={},
    )
    result = ranker.retrieve("Show its status", limit=3)
    assert {s["source_path"] for s in result["segments"]} == set(texts)
    assert result["candidate_accounting"]["route_counts"]["claim_context"] == 3
    assert result["bundles"][0]["claim_context"]["omitted_related_segments"] == 0
    assert result["bundles"][0]["claim_context"]["continuations"][0]["kind"] == "evidence_set"
    limited = ranker.retrieve("Show its status", limit=1)
    assert len(limited["segments"]) == 1
    assert limited["omitted_bundles"][0]["claim_context"]["related_segments"] == 3


def test_calendar_rule_proof_retracts_after_source_change_without_rewriting_history(vault):
    root, store = vault
    (root / "a.txt").write_text("Order 1847 was approved on 12 March 2026.")
    (root / "b.txt").write_text("Die Bestellung 1848 wurde am 13. März 2026 genehmigt.")
    first = ingest(store)

    def proofs(sid):
        return [
            edge for edge in knowledge.load(store, sid)["edges"] if edge["relation"] == "CALENDAR_PRECEDES"
        ]

    original = proofs(first)
    assert len(original) == 1
    assert original[0]["proof"] == {"earlier_day": "2026-03-12", "later_day": "2026-03-13"}
    assert original[0]["epistemic_status"] == "logical_derivation"
    assert original[0]["premises"] == [original[0]["from_node"], original[0]["to_node"]]
    (root / "b.txt").write_text("Die Bestellung 1848 wurde am 11. März 2026 genehmigt.")
    changed = ingest(store)
    assert proofs(changed)[0]["proof"] == {"earlier_day": "2026-03-11", "later_day": "2026-03-12"}
    assert proofs(first) == original
    (root / "b.txt").unlink()
    removed = ingest(store)
    assert proofs(removed) == []
    knowledge.verify(store, removed)


def test_observation_budget_stops_generator_before_unbounded_materialization(vault, monkeypatch):
    root, store = vault
    (root / "long.txt").write_text("March " * 10000)
    monkeypatch.setattr(knowledge, "MAX_OBSERVATIONS", 3)
    monkeypatch.setattr(knowledge, "nlp_observations", lambda text: ([], {}))
    original = knowledge.span_observations
    consumed = []

    def bounded(text):
        for row in original(text):
            consumed.append(row)
            assert len(consumed) <= 4, "Extractor must stop consuming beyond the visible budget"
            yield row

    monkeypatch.setattr(knowledge, "span_observations", bounded)
    sid = ingest(store)
    result = API(store).knowledge_query(sid, kind="observation")
    assert result["total"] == 3
    assert result["coverage"]["gaps"]["observation_budget_unexamined_sections"] == 1
    artifact = knowledge.read_knowledge(store, store.manifest(sid)["documents"][0]["knowledge"]["blob"])
    assert len(artifact["observations"]) == 3


def test_contact_only_neighbors_have_complete_relationship_continuation(vault):
    root, store = vault
    for name, person in [("a.txt", "Andreas Müller"), ("b.txt", "Andreas Mueller")]:
        (root / name).write_text(f"Name: {person}; email: a@example.test; organisation: Example AG")
    sid = ingest(store)
    manifest, segments, links = load_sources(store, sid)
    manifest["hybrid_manifest_sha"] = store.snapshot(sid)["manifest_sha"]
    seed = next(s["id"] for s in segments.values() if s["source_path"] == "a.txt")
    ranker = HybridDiscovery(
        None,
        sid,
        lambda q: [(seed, 1.0)],
        Ranking(),
        Worksets(),
        sources=(manifest, segments, links),
        typed=[],
        lexical=EmptyLexical(),
        model_identity={},
    )
    result = ranker.retrieve("Show this contact", limit=1)
    bundles = result["bundles"] + result["omitted_bundles"]
    continuation = next(
        c for b in bundles for c in b["claim_context"]["continuations"] if c["kind"] == "edge"
    )
    params = {key: value for key, value in continuation.items() if key != "tool"}
    page = API(store).knowledge_query(**params, limit=1)
    edges = page["items"][:]
    while page["next_cursor"]:
        page = API(store).knowledge_query(**params, limit=1, cursor=page["next_cursor"])
        edges.extend(page["items"])
    comparisons = [e for e in edges if e["relation"] == "IDENTITY_COMPARISON"]
    assert len(comparisons) == 2
    assert {r["segment_id"] for e in comparisons for r in e["related_sources"]} == set(segments)
