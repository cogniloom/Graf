"""NEW discovery policy contracts on captured synthetic inputs only."""

from email.message import EmailMessage

import pytest

from evidencekg.config import configure
from evidencekg.db import dump
from evidencekg.ingest import ingest
from evidencekg.ranking import RankedDiscovery
from evidencekg.reference_ranking import ReferenceRanking
from evidencekg.retrieval import API


def passages(store, snapshot):
    api = API(store)
    return {
        doc["path"]: [
            api.segment(snapshot, row["id"])
            for row in api.sections(snapshot, doc["document_version_id"])["items"]
        ]
        for doc in store.manifest(snapshot)["documents"]
        if doc["extraction_id"]
    }


def assert_packet(result, store, snapshot):
    assert result["output_bytes"] == len(dump(result).encode()) <= 60000
    assert result["evidence_bytes"] == len(
        dump({"segments": result["segments"], "reasons": result["reasons"]}).encode()
    )
    assert result["selected_count"] == len(result["segments"])
    assert result["omitted_count"] == result["candidate_counts"]["union"] - result["selected_count"]
    assert sum(result["remaining"].values()) == result["omitted_count"]
    for segment in result["segments"]:
        assert segment == API(store).segment(snapshot, segment["id"])


@pytest.mark.parametrize("limit", [1, 3, 6, 7, 12])
def test_baseline_original_prefix_preserved(vault, limit):
    root, store = vault
    for i in range(9):
        (root / f"{i}.txt").write_text(f"violet docket unique{i}")
    snapshot = ingest(store)
    baseline = ReferenceRanking(store, snapshot).retrieve("violet", "baseline", 6)
    original = dump(baseline)
    discovery = RankedDiscovery(store, snapshot)
    result = discovery.retrieve("violet", limit, baseline=baseline)
    assert result["segments"][: min(6, limit)] == baseline["segments"][: min(6, limit)]
    assert dump(baseline) == original
    assert result == discovery.retrieve("violet", limit)
    assert_packet(result, store, snapshot)


def test_weak_hubs_cannot_fill_results_or_hide_strong_identifier(vault):
    root, store = vault
    boilerplate = "Alex Smith common@example.org 2026-09-25\n\nConfidential standard footer."
    (root / "seed.txt").write_text("violet dispute Case: 123\n\n" + boilerplate)
    (root / "strong.txt").write_text("Case: 123\n\nThe signed response is attached.")
    for i in range(20):
        (root / f"hub{i}.txt").write_text(f"Unrelated matter {i}.\n\n" + boilerplate)
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve("violet dispute")
    assert len(result["segments"]) == 2
    assert result["segments"][1]["text"].startswith("Case: 123")
    assert any(r["type"] == "typed_identifier" for r in result["reasons"][result["segments"][1]["id"]])
    assert result["candidate_counts"]["mechanical"] == 1
    assert_packet(result, store, snapshot)


def test_graph_has_no_reserved_slots(vault):
    root, store = vault
    for i in range(9):
        (root / f"lexical{i}.txt").write_text(f"violet dispute unique{i}" + (" Case: 123" if i == 0 else ""))
    (root / "graph.txt").write_text("Case: 123\n\nResponse without query terms.")
    for i in range(10):
        (root / f"filler{i}.txt").write_text(f"unrelated {i}")
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve("violet dispute", 8)
    assert all("violet dispute" in s["text"] for s in result["segments"])
    assert result["candidate_counts"]["mechanical"] == 1


@pytest.mark.parametrize(
    "relationship", ["ATTACHMENT_OF", "EMAIL_REPLY_REFERENCE", "EXPLICIT_DOCUMENT_REFERENCE"]
)
def test_explicit_endpoint_without_evidence_and_single_hop(vault, relationship):
    root, store = vault
    for name, text in [
        ("seed", "violet"),
        ("target", "Only a signed response."),
        ("hop2", "Never traverse two hops."),
    ]:
        (root / f"{name}.txt").write_text(text)
    snapshot = ingest(store)
    source = passages(store, snapshot)
    with store.write():
        for i, (left, right) in enumerate([("seed", "target"), ("target", "hop2")]):
            store.db.execute(
                "INSERT INTO explicit_links VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"L{i}",
                    source[left + ".txt"][0]["document_version_id"],
                    relationship,
                    source[right + ".txt"][0]["document_version_id"],
                    "resolved",
                    "{}",
                    "synthetic",
                    "test-v1",
                ),
            )
            store.db.execute("INSERT INTO snapshot_links VALUES(?,?)", (snapshot, f"L{i}"))
    result = RankedDiscovery(store, snapshot).retrieve("violet")
    assert {s["text"] for s in result["segments"]} == {"violet", "Only a signed response."}
    assert_packet(result, store, snapshot)


def test_real_email_attachment_and_reply_endpoint_originals(vault):
    root, store = vault
    parent = EmailMessage()
    parent["Message-ID"] = "<parent@example.org>"
    parent.set_content("violet docket")
    parent.add_attachment(b"The signed response.", maintype="text", subtype="plain", filename="response.txt")
    (root / "parent.eml").write_bytes(parent.as_bytes())
    reply = EmailMessage()
    reply["In-Reply-To"] = "<parent@example.org>"
    reply.set_content("We did not agree.")
    (root / "reply.eml").write_bytes(reply.as_bytes())
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve("violet docket")
    assert any(s["text"] == "The signed response." for s in result["segments"])
    assert any("We did not agree." in s["text"] for s in result["segments"])
    assert_packet(result, store, snapshot)


def test_exact_copies_dedup_only_supplements_never_negation(vault):
    root, store = vault
    for i in range(10):
        (root / f"copy{i}.txt").write_text("violet agreed.")
    (root / "negated.txt").write_text("violet not agreed.")
    snapshot = ingest(store)
    source = passages(store, snapshot)
    baseline = {"items": [source[f"copy{i}.txt"][0] for i in range(6)]}
    discovery = RankedDiscovery(store, snapshot)
    result = discovery.retrieve("violet", baseline=baseline)
    assert result["segments"][:6] == baseline["items"]
    assert len(result["segments"]) == 7
    assert result["segments"][-1]["text"] == "violet not agreed."
    assert result["remaining"]["exact_duplicate"] == 4
    alias = result["duplicate_aliases"][baseline["items"][0]["id"]]
    assert alias["count"] == 10 and alias["remaining"] == 2
    assert len(discovery.duplicate_aliases(baseline["items"][0]["id"])) == 10
    assert_packet(result, store, snapshot)


def test_identical_passage_with_different_context_is_retained(vault):
    root, store = vault
    configure(store, {"segment_chars": 32, "overlap_chars": 0})
    (root / "a.txt").write_text("violet agreed.\n\n" + "A" * 50)
    (root / "b.txt").write_text("violet agreed.\n\n" + "B" * 50)
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve("violet", baseline={"segments": []})
    assert len(result["segments"]) == 2
    assert result["remaining"]["exact_duplicate"] == 0


def test_utf8_full_output_budget_with_no_truncation(vault):
    root, store = vault
    for i in range(30):
        (root / f"large{i}.txt").write_text(f"violet unique{i} " + "über " * 650)
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve("violet", 1000)
    assert result["baseline_preserved_count"] == 6
    assert 6 <= result["selected_count"] < 30
    assert result["remaining"]["byte_budget"] > 0
    assert all(s["text"].endswith("über " * 650) for s in result["segments"])
    assert_packet(result, store, snapshot)


def test_impossible_supplied_prefix_fails_explicitly(vault, monkeypatch):
    root, store = vault
    (root / "a.txt").write_text("violet " * 100)
    snapshot = ingest(store)
    baseline = {"segments": passages(store, snapshot)["a.txt"]}
    monkeypatch.setattr("evidencekg.ranking.MAX_OUTPUT_BYTES", 500)
    with pytest.raises(ValueError, match="Preserved baseline cannot fit"):
        RankedDiscovery(store, snapshot).retrieve("violet", baseline=baseline)


def test_empty_attachment_is_visible_gap_not_answer_passage(vault):
    root, store = vault
    mail = EmailMessage()
    mail["Subject"] = "violet consent"
    mail.set_content("violet consent requires a signed attachment")
    mail.add_attachment(b"", maintype="application", subtype="octet-stream", filename="empty.xyz")
    (root / "mail.eml").write_bytes(mail.as_bytes())
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve("violet consent")
    assert result["remaining"]["empty_text"] >= 1
    assert all(s["text"].strip() for s in result["segments"])
    assert any(d["status"] == "unsupported" for d in store.manifest(snapshot)["documents"])
    assert_packet(result, store, snapshot)


def test_dense_locators_do_not_displace_protected_original_text(vault):
    from docx import Document

    root, store = vault
    doc = Document()
    for i in range(450):
        doc.add_paragraph(f"violet {i}")
    doc.save(root / "dense.docx")
    snapshot = ingest(store)
    source = passages(store, snapshot)["dense.docx"][0]
    assert len(dump(source["locators"]).encode()) > 1024
    result = RankedDiscovery(store, snapshot).retrieve("violet", baseline={"segments": [source]})
    selected = result["segments"][0]
    assert selected["text"] == source["text"]
    assert selected["id"] == source["id"]
    assert selected["locators"] == source["locators"][: len(selected["locators"])]
    assert selected["locators_remaining"] == len(source["locators"]) - len(selected["locators"])
    continuation = dict(selected["locators_continuation"])
    assert continuation.pop("tool") == "segment_locators"
    full, cursor = [], None
    while True:
        page = API(store).segment_locators(**continuation, cursor=cursor)
        full.extend(item["location"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert full == source["locators"]
    assert result["output_bytes"] == len(dump(result).encode()) <= 60000


def test_deterministic_read_only_snapshot_scope(vault):
    root, store = vault
    (root / "seed.txt").write_text("violet Case: 123")
    (root / "target.txt").write_text("Case: 123 signed response")
    snapshot = ingest(store)
    discovery = RankedDiscovery(store, snapshot)
    expected = discovery.retrieve("violet")
    (root / "target.txt").write_text("Case: 123 foreign newer response")
    new_snapshot = ingest(store)
    writes = store.db.total_changes
    assert dump(expected) == dump(discovery.retrieve("violet"))
    assert dump(expected) == dump(RankedDiscovery(store, snapshot).retrieve("violet"))
    assert store.db.total_changes == writes
    assert "foreign newer" not in dump(expected)
    foreign = passages(store, new_snapshot)["target.txt"][0]
    with pytest.raises(ValueError, match="original snapshot"):
        discovery.retrieve("violet", baseline={"segments": [foreign]})
    with pytest.raises(ValueError, match="snapshot mismatch"):
        discovery.retrieve("violet", baseline={"snapshot_id": new_snapshot, "segments": []})


def test_observation_terms_are_attributed_and_return_originals(vault):
    root, store = vault
    (root / "de.txt").write_text("Die Zustimmung wurde verweigert.")
    snapshot = ingest(store)
    original = passages(store, snapshot)["de.txt"][0]
    observation = {
        "search_text": "consent refused",
        "terms": ["rejection"],
        "provenance": {"provider": "synthetic", "model": "fixture"},
    }
    discovery = RankedDiscovery(store, snapshot, {original["id"]: observation})
    observation["search_text"] = "mutated"
    result = discovery.retrieve("consent refused")
    assert result["segments"] == [original]
    assert result["candidate_counts"]["observations"] == 1
    assert result["candidate_counts"]["lexical"] == 0
    assert result["candidate_counts"]["mechanical"] == 0
    assert result["reasons"][original["id"]][0]["provenance"]["provider"] == "synthetic"
    assert_packet(result, store, snapshot)
    with pytest.raises(ValueError, match="attribution"):
        RankedDiscovery(store, snapshot, {original["id"]: {"terms": ["consent"]}})
    with pytest.raises(ValueError, match="outside snapshot"):
        RankedDiscovery(store, snapshot, {"foreign": observation})


@pytest.mark.parametrize(
    "key",
    [
        "segment_id",
        "snapshot_id",
        "extraction_id",
        "document_version_id",
        "text_sha",
        "source_text_sha",
        "source_path",
    ],
)
def test_observation_source_identity_is_checked(vault, key):
    root, store = vault
    (root / "a.txt").write_text("Die Zustimmung wurde verweigert.")
    snapshot = ingest(store)
    original = passages(store, snapshot)["a.txt"][0]
    observation = {
        "terms_de": ["Zustimmung"],
        "terms_en": ["consent"],
        "provenance": {"provider": "fixture"},
        key: "wrong",
    }
    with pytest.raises(ValueError, match="source identity mismatch"):
        RankedDiscovery(store, snapshot, {original["id"]: observation})
    observation.pop(key)
    observation["source_identity"] = {key: "wrong"}
    with pytest.raises(ValueError, match="source identity mismatch"):
        RankedDiscovery(store, snapshot, {original["id"]: observation})


def test_bilingual_term_only_observation_and_exact_source_spans(vault):
    root, store = vault
    (root / "a.txt").write_text("Die Zustimmung wurde verweigert.")
    snapshot = ingest(store)
    original = passages(store, snapshot)["a.txt"][0]
    ref = {
        "segment_id": original["id"],
        "extraction_id": original["extraction_id"],
        "start": 4,
        "end": 14,
        "quote": "Zustimmung",
    }
    observation = {
        "terms_de": ["Zustimmung"],
        "terms_en": ["consent"],
        "sources": [ref],
        "source_text_sha": original["text_sha"],
        "source_path": "a.txt",
        "provenance": {"provider": "fixture", "snapshot_id": snapshot},
    }
    assert RankedDiscovery(store, snapshot, {original["id"]: observation}).retrieve("consent")[
        "segments"
    ] == [original]
    ref["quote"] = "Fabricated"
    with pytest.raises(ValueError, match="Fabricated"):
        RankedDiscovery(store, snapshot, {original["id"]: observation})


@pytest.mark.parametrize("question", ["nohits", "the und oder"])
def test_empty_snapshot_and_no_matches(vault, question):
    _, store = vault
    snapshot = ingest(store)
    result = RankedDiscovery(store, snapshot).retrieve(question)
    assert result["segments"] == []
    assert result["candidate_counts"]["union"] == 0
    assert_packet(result, store, snapshot)


@pytest.mark.parametrize(
    "question,limit", [("", 12), (" ", 12), (None, 12), ("a" * 4097, 12), ("valid", 0), ("valid", True)]
)
def test_input_validation(vault, question, limit):
    _, store = vault
    snapshot = ingest(store)
    with pytest.raises(ValueError):
        RankedDiscovery(store, snapshot).retrieve(question, limit)
