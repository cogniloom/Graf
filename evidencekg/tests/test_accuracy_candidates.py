"""Synthetic original-source candidate contracts, not semantic recall proof."""

from email.message import EmailMessage

import pytest

from evidencekg.accuracy_candidates import CandidateCollector
from evidencekg.config import configure
from evidencekg.db import dump
from evidencekg.ingest import ingest
from evidencekg.retrieval import API


def docs(store, snapshot):
    return [d["document_version_id"] for d in store.manifest(snapshot)["documents"]]


def test_translation_originals_and_no_mutation(vault):
    root, store = vault
    (root / "brief.txt").write_text("Die Kündigung ist unwirksam. Keine Zustimmung wurde erteilt.")
    (root / "other.txt").write_text("Unrelated swimming competition")
    snapshot = ingest(store)
    before = "\n".join(store.db.iterdump())
    changes = store.db.total_changes
    plan = {"queries": ["Kündigung unwirksam"], "phrases": ["Keine Zustimmung"], "facets": []}
    frozen = dump(plan)
    collector = CandidateCollector(store, snapshot)
    result = collector.collect("Was the termination valid?", plan)
    assert any("Kündigung" in s["text"] for s in result["segments"])
    assert result == collector.collect("Was the termination valid?", plan)
    assert dump(plan) == frozen
    assert all(s == API(store).segment(snapshot, s["id"]) for s in result["segments"])
    assert store.db.total_changes == changes
    assert "\n".join(store.db.iterdump()) == before
    assert result["metadata"][result["segments"][0]["id"]]["source_path"] == "brief.txt"


def test_later_attachment_sections_and_fair_bounded_pool(vault):
    root, store = vault
    configure(store, {"segment_chars": 64, "overlap_chars": 0})
    parent = EmailMessage()
    parent["Message-ID"] = "<seed@example.org>"
    parent.set_content("violet docket")
    parent.add_attachment(
        ("ordinary attachment material. " * 30 + "LATER decisive denial.").encode(),
        maintype="text",
        subtype="plain",
        filename="response.txt",
    )
    (root / "parent.eml").write_bytes(parent.as_bytes())
    (root / "independent.txt").write_text("violet docket independent")
    snapshot = ingest(store)
    collector = CandidateCollector(store, snapshot)
    result = collector.collect("violet docket", {}, max_candidates=100)
    later = [s for s in result["segments"] if "decisive denial" in s["text"]]
    assert later
    assert any(r["type"] == "resolved_endpoint" for r in result["reasons"][later[0]["id"]])
    short = collector.collect("violet docket", {}, max_candidates=3)
    assert len({s["document_version_id"] for s in short["segments"]}) == 3
    represented = {s["id"] for s in short["segments"]}
    omitted = [sid for ids in short["remaining"].values() for sid in ids]
    assert not represented.intersection(omitted)
    assert len(represented) + len(omitted) == short["candidate_counts"]["scope_units"]
    assert later[0]["id"] in represented.union(omitted)


def test_complete_ten_document_scope_and_rejection(vault):
    root, store = vault
    for i in range(10):
        (root / f"{i}.txt").write_text("unfeatured singleton" if i == 9 else f"routine item {i}")
    snapshot = ingest(store)
    scope = docs(store, snapshot)
    collector = CandidateCollector(store, snapshot)
    result = collector.collect("no matches", {}, scope_document_ids=scope)
    assert len(result["segments"]) == 10
    assert any(s["text"] == "unfeatured singleton" for s in result["segments"])
    assert not result["rejected"] and not result["omitted_count"]
    for limits in ({"max_candidates": 9}, {"document_limit": 9}):
        rejected = collector.collect("no matches", {}, scope_document_ids=scope, **limits)
        assert rejected["rejected"] and not rejected["segments"]
        assert len(rejected["remaining"]["scope_over_limit"]) == 10
    with pytest.raises(ValueError, match="foreign"):
        collector.collect("query", {}, scope_document_ids=[*scope, "foreign"])


def test_same_subject_different_dates_and_exact_copies_retained(vault):
    root, store = vault
    for i, date in enumerate(("2026-01-01", "2026-02-01", "2026-02-01")):
        (root / f"{i}.txt").write_text(f"Subject: violet dispute\nDate: {date}\nNo consent.")
    snapshot = ingest(store)
    result = CandidateCollector(store, snapshot).collect("violet dispute", {})
    assert len(result["segments"]) == 3
    assert len({s["document_version_id"] for s in result["segments"]}) == 3


@pytest.mark.parametrize(
    "plan",
    [
        None,
        [],
        {"unknown": []},
        {"queries": "x"},
        {"queries": ["x"] * 17},
        {"phrases": ["x"] * 13},
        {"facets": ["x"] * 9},
        {"queries": ["x" * 201]},
        {"facets": ["x" * 301]},
        {"queries": [3]},
        {"queries": [""]},
        {"phrases": ["***"]},
    ],
)
def test_malformed_plan_refused(vault, plan):
    root, store = vault
    (root / "x.txt").write_text("violet")
    collector = CandidateCollector(store, ingest(store))
    with pytest.raises(ValueError):
        collector.collect("violet", plan)


def test_fts_operators_are_literal_phrase_tokens(vault):
    root, store = vault
    (root / "x.txt").write_text("violet")
    (root / "y.txt").write_text("amber")
    result = CandidateCollector(store, ingest(store)).collect("unknown", {"phrases": ["violet OR amber"]})
    # Individual recall terms may hit, but the supplied phrase is not an OR expression.
    phrase = [p for p in result["candidate_counts"]["probes"] if p["phrase"]]
    assert phrase[0]["matches"] == 0


def test_scope_graph_cannot_leak_and_locator_continuation(vault):
    root, store = vault
    from docx import Document

    document = Document()
    for _ in range(90):
        document.add_paragraph("violet docket")
    document.save(root / "seed.docx")
    (root / "foreign.txt").write_text("Outside explicit benchmark scope")
    snapshot = ingest(store)
    collector = CandidateCollector(store, snapshot)
    seed = next(
        s
        for s in collector.units
        if collector.documents[collector.units[s]["document_version_id"]] == "seed.docx"
    )
    seed_doc = collector.units[seed]["document_version_id"]
    other_doc = next(d for d in collector.documents if d != seed_doc)
    # Real dense DOCX locators and a synthetic out-of-scope endpoint.
    original = API(store).segment(snapshot, seed)
    locators = original["locators"]
    with store.write():
        store.db.execute(
            "INSERT INTO explicit_links VALUES(?,?,?,?,?,?,?,?)",
            ("test-link", seed_doc, "ATTACHMENT_OF", other_doc, "resolved", "{}", "synthetic", "test-v1"),
        )
        store.db.execute("INSERT INTO snapshot_links VALUES(?,?)", (snapshot, "test-link"))
    collector = CandidateCollector(store, snapshot)
    before = "\n".join(store.db.iterdump())
    result = collector.collect("violet", {}, scope_document_ids=[seed_doc])
    assert result["scope_document_ids"] == [seed_doc]
    assert [s["id"] for s in result["segments"]] == [seed]
    segment = result["segments"][0]
    assert segment["text"] == original["text"]
    assert segment["locators"] == locators[: len(segment["locators"])]
    assert segment["locators_remaining"] == 90 - len(segment["locators"])
    continuation = segment["locators_continuation"]
    assert continuation.pop("method") == "segment_locators"
    assert [r["location"] for r in API(store).segment_locators(**continuation)["items"]] == locators
    assert "\n".join(store.db.iterdump()) == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"question": ""},
        {"question": "x" * 4097},
        {"question": 7},
        {"max_candidates": True},
        {"document_limit": 0},
        {"scope_document_ids": []},
    ],
)
def test_invalid_request_refused(vault, kwargs):
    root, store = vault
    (root / "x.txt").write_text("violet")
    collector = CandidateCollector(store, ingest(store))
    request = {"question": "violet", "plan": {}, **kwargs}
    with pytest.raises(ValueError):
        collector.collect(**request)


def test_selected_segment_matches_preserved_extraction(vault):
    from evidencekg.db import sha

    root, store = vault
    (root / "original.txt").write_text("No consent was given.")
    snapshot = ingest(store)
    sid = store.one("SELECT id FROM segments")["id"]
    forged = "Consent was given."
    store.db.execute("UPDATE segments SET text=?,text_sha=? WHERE id=?", (forged, sha(forged), sid))
    with pytest.raises(ValueError, match="immutable extraction"):
        CandidateCollector(store, snapshot).collect("consent", {}, scope_document_ids=docs(store, snapshot))


@pytest.mark.parametrize("field,value", [("locator_json", "[]"), ("ordinal", 7)])
def test_selected_locator_and_segment_identity_provenance(vault, field, value):
    root, store = vault
    (root / "original.txt").write_text("No consent was given.")
    snapshot = ingest(store)
    # Field is a test-only fixed vocabulary, never source/MCP input.
    store.db.execute(f"UPDATE segments SET {field}=?", (value,))
    with pytest.raises(ValueError, match="provenance drift"):
        CandidateCollector(store, snapshot).collect("consent", {}, scope_document_ids=docs(store, snapshot))
