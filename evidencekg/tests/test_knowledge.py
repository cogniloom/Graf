import json
import subprocess
import sys

import pytest

from evidencekg import knowledge
from evidencekg.config import initialize
from evidencekg.hybrid.sources import load_sources
from evidencekg.ingest import ingest
from evidencekg.retrieval import API


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    store = initialize(tmp_path / "state", root, {"ocr": "off"})
    yield root, store
    store.close()


@pytest.mark.parametrize(
    "line,polarity,modality,condition",
    [
        ("Alice approved order 1847.", "positive", "asserted", None),
        ("Alice did not approve order 1847.", "negative", "asserted", None),
        (
            "Alice will approve order 1847 only after inspection.",
            "positive",
            "planned",
            "only after inspection",
        ),
        ("Order 1847 was not approved.", "negative", "asserted", None),
        ("Invoice INV-12 must be paid if delivery occurs.", "positive", "required", "if delivery occurs"),
        (
            "Order 1847 was approved only after payment of CHF 1.50.",
            "positive",
            "asserted",
            "only after payment of CHF 1.50",
        ),
        ("Order 1847 may be approved.", "positive", "possible", None),
    ],
)
def test_polarity_modality_and_complete_qualifiers(line, polarity, modality, condition):
    units = list(knowledge.clauses(line))
    assert units == [(0, len(line), line)]
    claim = knowledge.parse_clause(line)
    assert (claim["polarity"], claim["modality"], claim["condition"]) == (polarity, modality, condition)


@pytest.mark.parametrize(
    "line",
    [
        "Order 1847 was approved?",
        "Order 1847 was approved?!",
        '"Order 1847 was approved?"',
        "Alice denied approving order 1847.",
        "Order 1847 approval requested.",
        "Order 1847 approval is no longer required.",
        "Someone claims order 1847 was approved.",
        "Order 1847 was approved by Nobody on 2026-05-12.",
        "Order 1847 was approved by no one.",
        "Order 1847 was approved by No One.",
        "Order 1847 was approved on 2026-02-30.",
        "Order 1847 was approved. That statement is false.",
        "Alice approved order 1847 and denied it later.",
    ],
)
def test_ambiguous_or_unsupported_grammar_abstains(line):
    assert knowledge.parse_clause(line) is None


def test_quotes_and_attribution_are_not_promoted(vault):
    root, store = vault
    (root / "quote.txt").write_text(
        '"Order 1847 was approved on 2026-05-12."\nOrder 1847 was not approved on 2026-05-12.'
    )
    sid = ingest(store)
    api = API(store)
    claims = api.knowledge_query(sid)["items"]
    assert len(claims) == 2
    quoted = next(c for c in claims if c["polarity"] == "positive")
    assert quoted["attribution"] == "quoted_speaker_unresolved"
    assert quoted["quote"].startswith('"')
    assert api.knowledge_query(sid, kind="conflict")["total"] == 0


def write_claims(root):
    texts = {
        "approve.txt": "Order 1847 was approved on 2026-05-12.",
        "deny.txt": "Order 1847 was not approved on 2026-05-12.",
        "future.txt": "Order 1847 will be approved on 2026-05-12 only after inspection.",
        "later.txt": "Order 1847 was not approved on 2026-05-13.",
        "unknown.txt": "Order 1847 was not approved.",
        "other.txt": "Order 1848 was approved on 2026-05-12.",
    }
    for name, text in texts.items():
        (root / name).write_text(text)


def test_frozen_graph_query_conflict_and_source_dependencies(vault):
    root, store = vault
    write_claims(root)
    for i in range(20):
        (root / f"copy-{i}.txt").write_bytes((root / "approve.txt").read_bytes())
    sid = ingest(store)
    api = API(store)
    result = api.knowledge_query(sid, entity="order:1847")
    assert result["total"] == 25
    assert {c["modality"] for c in result["items"]} == {"asserted", "planned"}
    conflicts = api.knowledge_query(sid, kind="conflict")["items"]
    assert len(conflicts) == 1 and conflicts[0]["claim_count"] == 22
    members = api.knowledge_query(sid, group_id=conflicts[0]["id"])["items"]
    assert {c["applicable_on"] for c in members} == {"2026-05-12"}
    assert all(c["modality"] == "asserted" and not c["condition"] for c in members)
    deps = api.knowledge_query(sid, kind="dependence")["items"]
    assert {c["method"] for c in deps} == {"same_source_bytes", "repeated_wording"}
    assert all(c["claim_count"] == 21 for c in deps)
    for claim in result["items"]:
        assert claim["epistemic_status"] == "extracted_claim"
        assert claim["confidence"]["claim_truth"]["calibrated_probability"] is None
        for ref in claim["sources"]:
            segment = api.segment(sid, ref["segment_id"])
            assert segment["text"][ref["start"] : ref["end"]] == ref["quote"]
    graph = knowledge.load(store, sid)
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["from_node"] in ids and e["to_node"] in ids for e in graph["edges"])
    # No quadratic pairwise corroboration/conflict edges.
    records = [n for n in graph["nodes"] if n["kind"] in {"claim", "observation"}]
    assert len(graph["edges"]) < 6 * len(records)
    assert all("corroboration" not in e["relation"].lower() for e in graph["edges"])
    knowledge.verify(store, sid)


def test_receipts_and_cursor_filter_binding(vault):
    root, store = vault
    write_claims(root)
    sid = ingest(store)
    api = API(store)
    result = api.knowledge_query(sid, entity="order:1847", limit=1)
    assert result["total"] == 5 and result["remaining"] == 4
    ids = {result["items"][0]["id"]}
    while result["next_cursor"]:
        result = api.knowledge_query(sid, entity="order:1847", cursor=result["next_cursor"], limit=1)
        ids.add(result["items"][0]["id"])
    assert len(ids) == 5
    cursor = api.knowledge_query(sid, entity="order:1847", limit=1)["next_cursor"]
    with pytest.raises(ValueError, match="cursor"):
        api.knowledge_query(sid, entity="order:1848", cursor=cursor)
    with pytest.raises(ValueError, match="cursor"):
        api.knowledge_query(sid, entity="order:1847", predicate="approval", cursor=cursor)
    assert api.knowledge_query(sid, entity="order:1847", applicable_on="2026-05-12")["total"] == 3
    with pytest.raises(ValueError):
        api.knowledge_query(sid, kind="conflict", entity="order:1847")


def test_incremental_update_delete_restore_and_clean_rebuild(vault, monkeypatch):
    root, store = vault
    write_claims(root)
    first = ingest(store)
    original_extract = knowledge.extract_document

    def unexpected(*args):
        raise AssertionError("unchanged extraction should be reused")

    monkeypatch.setattr(knowledge, "extract_document", unexpected)
    assert ingest(store) == first
    monkeypatch.setattr(knowledge, "extract_document", original_extract)
    (root / "deny.txt").unlink()
    second = ingest(store)
    assert API(store).knowledge_query(second, kind="conflict")["total"] == 0
    assert API(store).knowledge_query(first, kind="conflict")["total"] == 1
    (root / "deny.txt").write_text("Order 1847 was not approved on 2026-05-12.")
    third = ingest(store)
    assert API(store).knowledge_query(third, kind="conflict")["total"] == 1
    # A clean state for the same corpus/config must derive identical claims/graph.
    fresh = initialize(store.state.parent / "fresh", root, {"ocr": "off"})
    try:
        rebuilt = ingest(fresh)
        assert knowledge.load(fresh, rebuilt) == knowledge.load(store, third)
    finally:
        fresh.close()
    old_signature = knowledge.signature()
    monkeypatch.setattr(knowledge, "signature", lambda: old_signature | {"version": "test-v2"})
    fourth = ingest(store)
    assert fourth != third
    assert (
        store.manifest(third)["documents"][0]["extraction_id"]
        == store.manifest(fourth)["documents"][0]["extraction_id"]
    )
    assert knowledge.load(store, first)["signature"] == old_signature
    knowledge.verify(store, fourth)
    knowledge.verify(store, first)


def test_asr_uncertainty_and_withholding_are_preserved(vault, monkeypatch):
    from evidencekg.parsers.transcription import render_segments

    root, store = vault
    (root / "recording.txt").write_text("captured test source")
    words = [dict(start=0, end=1, word="Order 1847 was approved.", probability=0.83)]
    speech = dict(
        start=0,
        end=1,
        text=words[0]["word"],
        words=words,
        avg_logprob=-0.1,
        no_speech_prob=0.01,
        compression_ratio=1.0,
    )
    sections, _ = render_segments([speech], 2, 0.8)
    low = dict(speech, start=1, end=2, words=[dict(words[0], start=1, end=2, probability=0.2)])
    withheld, _ = render_segments([low], 2, 0.8)
    monkeypatch.setattr(
        "evidencekg.ingest.parsers.parse",
        lambda *args: dict(
            status="partial",
            warnings=["uncertain speech"],
            sections=sections + withheld,
            attachments=[],
            artifacts=[],
        ),
    )
    sid = ingest(store)
    result = API(store).knowledge_query(sid)
    assert result["total"] == 1
    claim = result["items"][0]
    fidelity = claim["confidence"]["transcription"]
    assert fidelity["raw"]["score"] == 0.83 and fidelity["calibrated_probability"] is None
    assert claim["locator"]["start_seconds"] == 0
    assert result["coverage"]["gaps"]["withheld_speech_sections"] == 1


def test_cross_segment_spans_visible_budget_gaps_and_source_annotations(vault, monkeypatch):
    root, store = vault
    # Force a primary split through a sentence; canonical source must stay intact.
    monkeypatch.setattr("evidencekg.ingest.split", lambda text, _: [(0, 17), (17, len(text))])
    (root / "long.txt").write_text("Order 1847 was approved only after payment of CHF 1.50.")
    sid = ingest(store)
    claim = API(store).knowledge_query(sid)["items"][0]
    assert len(claim["sources"]) == 2
    assert "".join(s["quote"] for s in claim["sources"]) == claim["quote"]
    manifest, segments, _ = load_sources(store, sid)
    assert all(s["knowledge"]["total"] == 1 for s in segments.values())
    assert knowledge.discovery_hint(manifest, sid, segments.values())["entities"] == ["order:1847"]
    monkeypatch.setattr(knowledge, "MAX_CLAIMS", 0)
    # Changed source forces fresh enrichment, exposing the budget rather than silently claiming completeness.
    (root / "long.txt").write_text("Order 1847 was not approved.")
    sid2 = ingest(store)
    result = API(store).knowledge_query(sid2)
    assert result["total"] == 0 and result["coverage"]["gaps"]["claim_limit"] == 1


def test_corrupt_knowledge_blob_fails_closed(vault):
    root, store = vault
    (root / "source.txt").write_text("Order 1847 was approved.")
    sid = ingest(store)
    digest = store.manifest(sid)["knowledge"]["blob"]
    (store.state / "objects" / digest[:2] / digest).write_text("{}")
    with pytest.raises(ValueError, match="Corrupt"):
        API(store).knowledge_query(sid)


def test_knowledge_annotations_reach_research_context(vault):
    from evidencekg.investigations.service import Investigations

    root, store = vault
    write_claims(root)
    sid = ingest(store)
    _, segments, _ = load_sources(store, sid)
    snapshot = {"snapshot_id": sid, "segments": segments, "selected_segments": list(segments)}
    context = Investigations._context(snapshot, "Was order 1847 approved?", None)
    items = [item for passage in context["passages"] for item in passage["knowledge"]["items"]]
    assert {item["polarity"] for item in items} == {"positive", "negative"}
    assert any(item["condition"] == "only after inspection" for item in items)
    assert any(group["kind"] == "conflict" for item in items for group in item["groups"])
    assert all(item["confidence"]["claim_truth"]["calibrated_probability"] is None for item in items)


def test_real_cli_knowledge(vault):
    root, store = vault
    write_claims(root)
    sid = ingest(store)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "evidencekg.cli",
            "--state",
            str(store.state),
            "knowledge",
            "--entity",
            "order:1847",
            "--limit",
            "2",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["total"] == 5 and payload["next_cursor"]
    assert payload["snapshot_id"] == sid


@pytest.mark.asyncio
async def test_real_mcp_knowledge_pagination(vault):
    from mcp import Client, StdioServerParameters

    root, store = vault
    write_claims(root)
    (root / "german.txt").write_text("Die Bestellung 1847 wurde am 12. Mai 2026 nicht genehmigt.")
    (root / "policy.txt").write_text("Richtlinie P1 gilt ab 1. März 2026 bis einschließlich 31. März 2026.")
    sid = ingest(store)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "evidencekg.cli", "--state", str(store.state), "serve", "--backend", "mechanical"],
    )
    async with Client(params, read_timeout_seconds=20) as client:
        tools = await client.list_tools()
        assert "knowledge_query" in {t.name for t in tools.tools}
        validity = await client.call_tool(
            "knowledge_query",
            {"snapshot_id": sid, "entity": "policy:P1", "valid_at": "12. März 2026"},
        )
        assert not validity.is_error
        assert validity.structured_content["total"] == 1
        result = await client.call_tool(
            "knowledge_query", {"snapshot_id": sid, "entity": "order:1847", "limit": 1}
        )
        assert not result.is_error
        data = result.structured_content
        assert data["total"] == 6
        next_page = await client.call_tool(
            "knowledge_query",
            {"snapshot_id": sid, "entity": "order:1847", "cursor": data["next_cursor"], "limit": 1},
        )
        assert not next_page.is_error
        assert next_page.structured_content["items"][0]["id"] != data["items"][0]["id"]
        mixed = await client.call_tool(
            "knowledge_query",
            {
                "snapshot_id": sid,
                "kind": "evidence_set",
                "entity": "Bestellung:1847",
                "applicable_on": "12. Mai 2026",
            },
        )
        assert not mixed.is_error
        assert mixed.structured_content["total"] == 6
        assert {row["language"] for row in mixed.structured_content["items"]} == {"en", "de"}
        assert mixed.structured_content["evidence_set"]["roles"]["time_unknown_or_ambiguous"] == 1
        months = await client.call_tool(
            "knowledge_query", {"snapshot_id": sid, "kind": "observation", "text": "May"}
        )
        assert not months.is_error and months.structured_content["total"] > 0
