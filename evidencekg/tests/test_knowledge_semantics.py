import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from test_knowledge import vault

from evidencekg import knowledge, knowledge_links
from evidencekg import knowledge_semantics as semantics
from evidencekg.config import DEFAULTS
from evidencekg.ingest import ingest
from evidencekg.parsers import parse
from evidencekg.parsers.structure import email_sections
from evidencekg.parsers.transcription import confidence, retry_uncertain
from evidencekg.retrieval import API

__all__ = ["vault"]


@pytest.mark.parametrize(
    "text",
    [
        "Alice paid CHF 50.25 to Bob for invoice INV-1 on 12 March 2026.",
        "Alice zahlte CHF 50.25 an Bob für Rechnung INV-1 am 12. März 2026.",
    ],
)
def test_payment_participants_are_one_source_bound_event(text):
    result = semantics.parse(text, knowledge.parse_clause)
    assert result["participants"] == {
        "payer": "Alice",
        "payee": "Bob",
        "invoice_or_subject": "invoice INV-1" if "paid" in text else "Rechnung INV-1",
    }
    assert result["amount"]["alternatives"] == ["50.25"]
    assert result["amount"]["currency"] == "CHF"
    assert result["applicable_on"] == "2026-03-12"
    assert result["predicate"] == "payment"


@pytest.mark.parametrize(
    "text",
    [
        "Order 12 was approved valid from 1 March 2026 through 31 March 2026.",
        "Die Bestellung 12 wurde genehmigt gültig vom 1. März 2026 bis einschließlich 31. März 2026.",
    ],
)
def test_validity_is_separate_from_event_day(text):
    result = semantics.parse(text, knowledge.parse_clause)
    assert result["applicable_on"] is None
    assert semantics.temporal_relation(result, "2026-03-31") == "within_stated_validity"
    assert semantics.temporal_relation(result, "2026-04-01") == "after_validity"


def test_policy_supersession_and_boundary_ambiguity(vault):
    root, store = vault
    (root / "policy.txt").write_text(
        "Richtlinie P1 gilt ab 1. März 2026 bis 31. März 2026. Policy P2 replaces policy P1 from 1 April 2026."
    )
    sid = ingest(store)
    rows = API(store).knowledge_query(sid)["items"]
    policy = next(r for r in rows if r["predicate"] == "validity")
    assert semantics.temporal_relation(policy, "2026-03-31") == "boundary_unresolved"
    edges = API(store).knowledge_query(sid, kind="edge")["items"]
    assert any(e["relation"] == "STATES_SUPERSESSION_OF" for e in edges)
    knowledge.verify(store, sid)


def test_multiple_sentences_preserve_dates_amounts_and_exact_offsets(vault):
    root, store = vault
    text = "Order 12 was approved on 12 March 2026. Die Bestellung 13 wurde am 12. März 2026 nicht genehmigt. Alice paid CHF 1.50 to Bob for invoice INV-1."
    (root / "mixed.txt").write_text(text)
    sid = ingest(store)
    rows = API(store).knowledge_query(sid)["items"]
    assert len(rows) == 3
    for row in rows:
        assert text[row["canonical_start"] : row["canonical_end"]] == row["quote"]
    knowledge.verify(store, sid)


def test_relative_date_does_not_use_outer_email_anchor_for_quotes():
    authored, quoted = list(
        email_sections(
            "Order 1 was approved yesterday.\n> Order 2 was approved yesterday.",
            "0",
            "Alice <a@example.test>",
            "Tue, 31 Mar 2026 14:00:00 +0200",
        )
    )
    first = semantics.parse(authored["text"], knowledge.parse_clause, authored["locator"])
    second = semantics.parse(quoted["text"], knowledge.parse_clause, quoted["locator"])
    assert first["applicable_on"] == "2026-03-30"
    assert second["applicable_on"] is None
    assert second["attribution"] == "quoted_speaker_unresolved"
    for quoted in ('"Order 1 was approved tomorrow."', 'Alice said: "Order 1 was approved tomorrow."'):
        candidate = semantics.parse(quoted, knowledge.parse_clause, authored["locator"])
        assert candidate["applicable_on"] is None
        assert candidate["attribution"] == "quoted_speaker_unresolved"


def docx(xml):
    from docx import Document

    output = io.BytesIO()
    Document().save(output)
    result = io.BytesIO()
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(result, "w") as target:
        for name in source.namelist():
            target.writestr(name, xml if name == "word/document.xml" else source.read(name))
    return result.getvalue()


def test_docx_deleted_negation_is_separate_and_current_revision_is_qualified(vault):
    root, store = vault
    data = docx(
        """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Order 1 was </w:t></w:r><w:del w:id="1" w:author="Alice"><w:r><w:delText>not </w:delText></w:r></w:del><w:r><w:t>approved.</w:t></w:r></w:p></w:body></w:document>"""
    )
    (root / "revision.docx").write_bytes(data)
    sid = ingest(store)
    rows = API(store).knowledge_query(sid)["items"]
    assert len(rows) == 1
    assert rows[0]["quote"] == "Order 1 was approved."
    assert rows[0]["modality"] == "unresolved"
    assert rows[0]["revision_state"] == "unaccepted_changes"
    assert API(store).knowledge_query(sid, kind="conflict")["total"] == 0
    knowledge.verify(store, sid)


def test_spreadsheet_header_formula_and_coordinate_metadata():
    from openpyxl import Workbook

    book = Workbook()
    book.active.append(["Rechnung", "Betrag (CHF)"])
    book.active.append(["INV-1", "=10+20"])
    data = io.BytesIO()
    book.save(data)
    result = parse(data.getvalue(), ".xlsx", DEFAULTS | {"ocr": "off"})
    cell = next(s for s in result["sections"] if s["locator"]["cell"] == "B2")
    assert cell["locator"]["header_candidate"] == "Betrag (CHF)"
    assert cell["locator"]["formula"] == "=10+20"
    assert cell["locator"]["cached_value"] is None


def test_identity_comparisons_preserve_contradictions_and_do_not_merge(vault):
    root, store = vault
    (root / "people.txt").write_text(
        "Name: Andreas Müller; E-Mail: a@example.test; Firma: Example AG\nName: Andreas Mueller; email: a@example.test; organisation: Example AG\nName: Anna Müller; email: a@example.test; organisation: Example AG"
    )
    sid = ingest(store)
    rows = API(store).knowledge_query(sid, kind="identity")["items"]
    assert len(rows) == 3
    assert sum(r["status"] == "same_identified_record" for r in rows) == 1
    assert sum(r["status"] == "conflicting_attributes" for r in rows) == 2
    assert all(r["calibrated_probability"] is None for r in rows)
    assert all(len(r["member_ids"]) == 2 for r in rows)
    knowledge.verify(store, sid)


def test_email_local_part_punctuation_is_not_identity_normalization():
    assert knowledge_links.field_value("email", "a.b@example.test") != knowledge_links.field_value(
        "email", "a-b@example.test"
    )


def test_near_copy_is_not_independent_support_and_retracts(vault):
    root, store = vault
    wording = (
        "This source describes a scheduled inspection of the shipment, the packaging, the documentation and the agreed delivery conditions for the customer. "
        * 3
    )
    (root / "a.txt").write_text(wording + "Order 1 was approved.")
    (root / "b.txt").write_text(wording + "Order 1 was not approved.")
    first = ingest(store)
    groups = API(store).knowledge_query(first, kind="dependence")["items"]
    assert any(g["method"] == "passage-shingle-jaccard-v1" for g in groups)
    assert (
        API(store).knowledge_query(first, kind="evidence_set", entity="order:1")["evidence_set"]["roles"][
            "dependence"
        ]
        == 2
    )
    (root / "b.txt").unlink()
    second = ingest(store)
    assert not any(
        g["method"] == "passage-shingle-jaccard-v1"
        for g in API(store).knowledge_query(second, kind="dependence")["items"]
    )
    knowledge.verify(store, first)
    knowledge.verify(store, second)


def test_overlapping_validity_conflict_keeps_both_sources(vault):
    root, store = vault
    (root / "a.txt").write_text("Order 1 was approved valid from 1 March 2026 through 31 March 2026.")
    (root / "b.txt").write_text(
        "Die Bestellung 1 wurde nicht genehmigt gültig ab 15. März 2026 bis einschließlich 15. April 2026."
    )
    sid = ingest(store)
    groups = API(store).knowledge_query(sid, kind="conflict")["items"]
    assert len(groups) == 1 and groups[0]["overlap_start"] == "2026-03-15"
    claims = API(store).knowledge_query(
        sid, kind="evidence_set", entity="order:1", applicable_on="20 March 2026"
    )["items"]
    assert all(c["validity_relation"] == "within_stated_validity" for c in claims)


def test_section_cache_reuses_text_but_rebinds_source_refs(vault, monkeypatch):
    root, store = vault
    (root / "a.txt").write_text("Order 1 was approved.")
    first = ingest(store)
    original = knowledge.semantics.parse
    calls = []

    def counted(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(knowledge.semantics, "parse", counted)
    (root / "b.txt").write_text("Order 1 was approved.")
    second = ingest(store)
    assert not calls
    rows = API(store).knowledge_query(second)["items"]
    assert len({r["document_version_id"] for r in rows}) == 2
    assert len({r["sources"][0]["segment_id"] for r in rows}) == 2
    knowledge.verify(store, first)
    assert calls  # Verification bypasses cached rule output.


def test_compressed_artifacts_remain_verifiable_and_read_legacy(vault):
    root, store = vault
    (root / "a.txt").write_text("Order 1 was approved.\n" * 50)
    sid = ingest(store)
    metadata = store.manifest(sid)["knowledge"]
    encoded = store.get(metadata["blob"])
    value = knowledge.load(store, sid)
    value = dict(value, nodes=list(value["nodes"]), edges=list(value["edges"]))
    assert len(encoded) < len(json.dumps(value).encode()) / 5
    old = store.put(json.dumps(value))
    assert knowledge.read_knowledge(store, old) == value
    knowledge.verify(store, sid)


def test_uncertain_negation_retry_is_audit_only_with_same_source_dependence():
    segment = dict(
        start=0.0,
        end=1.0,
        text=" not approved",
        avg_logprob=-0.2,
        no_speech_prob=0.0,
        compression_ratio=1.0,
        words=[dict(start=0.0, end=1.0, word=" not approved", probability=0.2)],
    )

    class Model:
        def transcribe(self, samples, **kwargs):
            assert kwargs["beam_size"] == 1
            return iter([SimpleNamespace(start=0.0, end=1.0, text=" approved", avg_logprob=-0.1)]), None

    result = retry_uncertain(Model(), [0] * 16000, [segment], 0.8, "en", 1)
    assert result["results"][0]["agrees_with_primary"] is False
    assert result["results"][0]["support_group"] == "same_recording_same_model"
    assert confidence(segment, 0.8)["withheld"]
    assert (
        retry_uncertain(Model(), [0] * 16000, [segment], 0.8, "de", 0)["results"][0]["status"]
        == "retry_budget_or_duration_limit"
    )


def test_quoted_paragraph_keeps_speaker_scope_across_sentences(vault):
    root, store = vault
    (root / "quoted.txt").write_text(
        'Alice said: "Contract 1 applies from 2026-01-01. Contract 2 supersedes Contract 1."'
    )
    sid = ingest(store)
    rows = API(store).knowledge_query(sid)["items"]
    assert len(rows) == 2
    assert all(row["attribution"] == "quoted_speaker_unresolved" for row in rows)
    assert all(row["speaker_surface"] == "Alice" for row in rows)


@pytest.mark.parametrize(
    "text,condition",
    [
        ("Order 1 was delivered only after invoice 9 was delivered.", "only after invoice 9 was delivered"),
        (
            "Die Bestellung 1 wurde geliefert, wenn Bestellung 9 geliefert wurde.",
            "wenn Bestellung 9 geliefert wurde",
        ),
        ("Order 1 was rejected only after invoice 9 was rejected.", "only after invoice 9 was rejected"),
    ],
)
def test_extended_verbs_never_rewrite_source_conditions(text, condition):
    assert semantics.parse(text, knowledge.parse_clause)["condition"] == condition


def test_html_email_blockquote_is_not_authored_or_outer_date_anchored():
    source = b"From: Alice <a@example.test>\nDate: Tue, 31 Mar 2026 14:00:00 +0200\nMIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n<p>My reply.</p><blockquote>Order 1 was approved tomorrow.</blockquote>"
    result = parse(source, ".eml", DEFAULTS)
    body = next(s for s in result["sections"] if "approved" in s["text"])
    assert body["locator"]["quotation_depth"] == 1
    claim = semantics.parse(body["text"], knowledge.parse_clause, body["locator"])
    assert claim["applicable_on"] is None


def test_retry_invalid_timing_abstains_before_decoding():
    class Model:
        def transcribe(self, *args, **kwargs):
            raise AssertionError("Must not decode invalid timing")

    row = dict(start=float("nan"), end=1.0, words=[], avg_logprob=-1, no_speech_prob=0, compression_ratio=1)
    assert retry_uncertain(Model(), [], [row], 0.8, "en")["results"][0]["status"] == "retry_invalid_alignment"


def test_shared_cache_rebinds_between_isolated_publications(tmp_path, monkeypatch):
    from evidencekg.config import initialize

    shared = tmp_path / "shared"
    for number in (1, 2):
        root = tmp_path / f"source-{number}"
        root.mkdir()
        (root / "a.txt").write_text("Order 1 was approved.")
        store = initialize(
            tmp_path / f"state-{number}", root, {"ocr": "off", "knowledge_cache_directory": str(shared)}
        )
        try:
            if number == 2:
                monkeypatch.setattr(
                    knowledge.semantics, "parse", lambda *args: pytest.fail("Expected shared section reuse")
                )
            sid = ingest(store)
            record = API(store).knowledge_query(sid)["items"][0]
            assert record["sources"][0]["extraction_id"] == record["extraction_id"]
            if number == 1:
                old = record
            else:
                assert record["document_version_id"] != old["document_version_id"]
                assert record["sources"] != old["sources"]
        finally:
            store.close()


def test_changed_paragraph_reuses_other_paragraphs_and_rebuilds_like_clean(vault, tmp_path, monkeypatch):
    from evidencekg.config import initialize

    root, store = vault
    source = root / "paragraphs.txt"
    source.write_text("Order 1 was approved.\n\nDie Bestellung 2 wurde genehmigt.")
    ingest(store)
    original = knowledge.semantics.parse
    calls = []

    def counted(*args):
        calls.append(args[0])
        return original(*args)

    with monkeypatch.context() as patch:
        patch.setattr(knowledge.semantics, "parse", counted)
        source.write_text("Order 1 was not approved.\n\nDie Bestellung 2 wurde genehmigt.")
        sid = ingest(store)
        assert calls == ["Order 1 was not approved."]
    incremental = API(store).knowledge_query(sid)["items"]
    clean = initialize(tmp_path / "clean-state", root, {"ocr": "off"})
    try:
        fresh = API(clean).knowledge_query(ingest(clean))["items"]

        def semantic(rows):
            return sorted(
                (r["quote"], r["predicate"], r["polarity"], r["modality"], r["condition"]) for r in rows
            )

        assert semantic(incremental) == semantic(fresh)
    finally:
        clean.close()
    knowledge.verify(store, sid)


def test_quote_attribution_survives_paragraph_cache_boundaries(vault):
    root, store = vault
    (root / "quotation.txt").write_text(
        'Alice said: "Order 1 was approved.\n\nOrder 2 was not approved." Order 3 was approved.'
    )
    sid = ingest(store)
    claims = API(store).knowledge_query(sid)["items"]
    assert len(claims) == 3
    quoted = [c for c in claims if "Order 3" not in c["quote"]]
    assert all(c["attribution"] == "quoted_speaker_unresolved" for c in quoted)
    assert all(c["speaker_surface"] == "Alice" for c in quoted)
    outside = next(c for c in claims if "Order 3" in c["quote"])
    assert outside["attribution"] == "source_document"
    knowledge.verify(store, sid)


def test_cache_reads_are_bounded(tmp_path):
    path = tmp_path / "index.json"
    path.write_bytes(b"x" * 1025)
    with pytest.raises(ValueError, match="read budget"):
        knowledge.read_cache_file(path, 1024)


def test_quoted_identity_records_remain_structure_qualified(vault):
    root, store = vault
    (root / "a.txt").write_text('Alice said: "\nName: Andreas Müller; email: a@example.test\n"')
    (root / "b.txt").write_text("Name: Andreas Mueller; email: a@example.test")
    sid = ingest(store)
    comparisons = API(store).knowledge_query(sid, kind="identity")["items"]
    assert len(comparisons) == 1
    assert comparisons[0]["status"] == "structure_qualified_identity"
    knowledge.verify(store, sid)
