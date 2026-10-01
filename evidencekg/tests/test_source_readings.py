"""Real PDF ingestion, immutable readings and optimistic append-only review."""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from evidencekg.app.api import create_app
from evidencekg.app.config import AppConfig
from evidencekg.app.manager import NotReady
from evidencekg.config import initialize
from evidencekg.ingest import ingest
from evidencekg.knowledge_readings import reference
from evidencekg.source_readings import (
    ReviewConflict,
    ReviewLedger,
    attach_reviews,
    overlay_knowledge,
    readings,
)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    pdf = Canvas(str(root / "letter.pdf"))
    pdf.drawString(60, 760, "Order 1847 was approved on 12 May 2026.")
    pdf.showPage()
    pdf.drawString(60, 760, "Invoice INV-12 was paid.")
    pdf.save()
    store = initialize(tmp_path / "vault", root, {"ocr": "off"})
    snapshot = ingest(store)
    doc = store.manifest(snapshot)["documents"][0]
    yield store, snapshot, doc
    store.close()


def save(ledger, db, item, **kwargs):
    return ledger.append(
        db,
        item,
        target_kind="reading",
        target_id=item["id"],
        decision="confirmed",
        reason="Compared page image",
        reviewer="Test reviewer",
        source_checked=True,
        **kwargs,
    )


def test_readings_review_corrections_and_claims_are_separate(corpus, tmp_path):
    store, snapshot, doc = corpus
    _, pages = readings(store, snapshot, doc["document_version_id"])
    assert len(pages) == 2
    page = pages[0]
    assert page["page"] == 1 and page["claims"]
    assert page["claims"][0]["source_reading"]["id"] == page["id"]
    original = store.get(page["artifact_sha"])
    ledger = ReviewLedger(tmp_path / "source-reading-reviews.sqlite3")
    with ledger.transaction() as db:
        confirmed = save(ledger, db, page)
        claim = page["claims"][0]
        accepted = ledger.append(
            db,
            page,
            target_kind="claim",
            target_id=claim["id"],
            decision="accepted",
            reason="Interpretation matches",
            reviewer="Reviewer",
            source_checked=True,
            reading_revision=confirmed["id"],
        )
        assert ledger.decorate(db, page)["claims"][0]["review_status"] == "accepted"
        correction = ledger.append(
            db,
            page,
            target_kind="reading",
            target_id=page["id"],
            decision="corrected",
            reason="Negation was missing",
            reviewer="Reviewer",
            source_checked=True,
            correction="Order 1847 was not approved on 12 May 2026.",
            expected_revision=confirmed["id"],
        )
        updated = ledger.decorate(db, page)
        assert updated["claims"][0]["review_status"] == "needs_reassessment"
        assert updated["claims"][0]["interpretation_review"]["id"] == accepted["id"]
        assert updated["truth_status"] == "unknown"
        assert updated["correction_candidates"][0]["polarity"] == "negative"
        assert updated["correction_candidates"][0]["reading_revision"] == correction["id"]
        assert "canonical_start" not in updated["correction_candidates"][0]
        assert len(updated["history"]) == 3
        with pytest.raises(ReviewConflict):
            save(ledger, db, page, expected_revision=confirmed["id"])
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("DELETE FROM reading_reviews")
    assert store.get(page["artifact_sha"]) == original
    projected = overlay_knowledge(tmp_path, store, snapshot, {"items": [dict(claim)]})
    assert projected["items"][0]["review_status"] == "needs_reassessment"
    segments = {s["id"]: s for s in store.rows("SELECT * FROM segments")}
    attach_reviews(tmp_path, segments)
    assert all("source_reading_reviews" in s for s in segments.values())
    assert segments[next(iter(segments))]["source_reading_reviews"]["records"][-1]["id"] == correction["id"]


def test_review_rejects_unbound_unchecked_or_unconfirmed_acceptance(corpus, tmp_path):
    store, snapshot, doc = corpus
    _, pages = readings(store, snapshot, doc["document_version_id"])
    page = pages[0]
    ledger = ReviewLedger(tmp_path / "reviews.sqlite3")
    with ledger.transaction() as db:
        args = dict(
            target_kind="claim",
            target_id=page["claims"][0]["id"],
            decision="accepted",
            reason="x",
            reviewer="x",
        )
        with pytest.raises(ValueError, match="Compare"):
            ledger.append(db, page, **args)
        with pytest.raises(ValueError, match="Confirm"):
            ledger.append(db, page, **args, source_checked=True)
        with pytest.raises(ValueError, match="does not belong"):
            ledger.append(db, pages[1], **args, source_checked=True)
        with pytest.raises(KeyError):
            readings(store, snapshot, "unknown")


def test_word_offsets_are_relative_to_full_page_not_paragraph():
    document = {"document_version_id": "d", "extraction_id": "e"}
    section = {
        "start": 110,
        "end": 150,
        "modality": "ocr",
        "locator": {
            "kind": "pdf",
            "page": 2,
            "reading_section_start": 100,
            "reading_section_end": 200,
            "reading": {
                "words": [{"start": 12, "end": 15, "score": 20}, {"start": 1, "end": 4, "score": 99}]
            },
        },
    }
    ref = reference(document, section, 110, 120)
    assert ref["flagged_spans"] == [{"start": 112, "end": 115, "score": 20}]
    assert ref["calibrated_probability"] is None


@pytest.fixture
def reading_client(corpus, tmp_path):
    store, snapshot, doc = corpus
    token = tmp_path / "token"
    token.write_text("t" * 48)
    token.chmod(0o600)
    ui = tmp_path / "ui"
    ui.mkdir()
    config = AppConfig(
        home=tmp_path / "app",
        database_config=tmp_path / "db.json",
        models=tmp_path / "models",
        token_file=token,
        ui_dist=ui,
    )
    row = dict(snapshot_id=snapshot, revision=1, publication={"state": str(store.state)})

    # Real extraction/store/routes/auth; only publication registry is a test double.
    class Publication:
        def __init__(self):
            self.config = config
            self.revoked = False
            self.after = False

        def evidence(self, operation):
            if self.revoked:
                raise NotReady("Sources changed")
            result = operation(row)
            if self.after:
                raise NotReady("Sources changed during review")
            return result

    manager = Publication()
    with TestClient(
        create_app(config, manager=manager, start_background=False, investigations=SimpleNamespace()),
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client, manager, doc, snapshot


def test_authenticated_read_render_review_and_source_fence(reading_client):
    client, manager, doc, snapshot = reading_client
    base = "/api/documents/" + doc["document_version_id"] + "/readings"
    auth = {"Authorization": "Bearer " + "t" * 48}
    assert client.get(base).status_code == 401
    page = client.get(base, headers=auth).json()["item"]
    image = base + "/" + page["id"] + "/image"
    response = client.get(image, params={"snapshot_id": snapshot}, headers=auth)
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\x89PNG")
    assert response.headers["cache-control"] == "no-store"
    assert client.get(image, params={"snapshot_id": "stale"}, headers=auth).status_code == 409
    post = base + "/" + page["id"] + "/reviews"
    body = dict(
        snapshot_id=snapshot,
        target_kind="reading",
        target_id=page["id"],
        decision="confirmed",
        reason="Viewed original page",
        reviewer="Tester",
        source_checked=True,
    )
    assert client.post(post, json=body).status_code == 401
    assert client.post(post, json=body, headers=auth | {"Origin": "http://evil.test"}).status_code == 403
    manager.after = True
    assert client.post(post, json=body, headers=auth).status_code == 409
    manager.after = False
    assert client.get(base, headers=auth).json()["item"]["history"] == []
    response = client.post(post, json=body, headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "confirmed"
    assert client.post(post, json=body, headers=auth).status_code == 409
    assert client.post(post, json=body | {"snapshot_id": "stale"}, headers=auth).status_code == 409
    manager.revoked = True
    assert client.get(base, headers=auth).status_code == 409
    assert client.get(image, params={"snapshot_id": snapshot}, headers=auth).status_code == 409
    assert client.post(post, json=body, headers=auth).status_code == 409


def test_empty_pages_have_distinct_reading_and_claim_identity():
    from evidencekg.segmentation import canonical
    from evidencekg.source_readings import reading_id

    document = dict(document_version_id="d", extraction_id="e")
    for kind, field in [("pdf", "page"), ("image", "frame")]:
        _, sections = canonical(
            [dict(text="", modality="ocr", locator={"kind": kind, field: n}) for n in (1, 2)]
        )
        assert sections[0]["start"] == sections[1]["start"] == 0
        ids = [reading_id(document, section) for section in sections]
        assert ids[0] != ids[1]
        assert [reference(document, s, 0, 0)["id"] for s in sections] == ids


def test_passage_projection_does_not_duplicate_ocr_word_geometry():
    from evidencekg.knowledge_readings import compact_locator

    locator = {
        "kind": "pdf",
        "page": 1,
        "reading": {
            "words": [{"text": "word", "start": 0, "end": 4, "bbox": [1, 2, 3, 4], "score": 20}],
            "method": "ocr",
        },
    }
    compact = compact_locator(locator)
    assert compact["reading"] == {"method": "ocr", "word_count": 1}
    assert locator["reading"]["words"][0]["text"] == "word"


def test_corrected_reading_allows_rejecting_old_claim_without_accepting_it(corpus, tmp_path):
    store, snapshot, doc = corpus
    _, pages = readings(store, snapshot, doc["document_version_id"])
    page = pages[0]
    ledger = ReviewLedger(tmp_path / "reviews.sqlite3")
    with ledger.transaction() as db:
        correction = ledger.append(
            db,
            page,
            target_kind="reading",
            target_id=page["id"],
            decision="corrected",
            reason="Missing negation",
            reviewer="Reviewer",
            source_checked=True,
            correction="Order 1847 was not approved on 12 May 2026.",
        )
        args = dict(
            target_kind="claim",
            target_id=page["claims"][0]["id"],
            reason="Claim conflicts with corrected reading",
            reviewer="Reviewer",
            source_checked=True,
            reading_revision=correction["id"],
        )
        with pytest.raises(ValueError, match="Confirm"):
            ledger.append(db, page, decision="accepted", **args)
        ledger.append(db, page, decision="rejected", **args)
        assert ledger.decorate(db, page)["claims"][0]["review_status"] == "rejected"


def test_corrected_claim_review_binds_correction_revision(corpus, tmp_path):
    store, snapshot, doc = corpus
    _, pages = readings(store, snapshot, doc["document_version_id"])
    page = pages[0]
    ledger = ReviewLedger(tmp_path / "reviews.sqlite3")
    with ledger.transaction() as db:
        correction = ledger.append(
            db,
            page,
            target_kind="reading",
            target_id=page["id"],
            decision="corrected",
            reason="Corrected negation",
            reviewer="Reviewer",
            source_checked=True,
            correction="Order 1847 was not approved.",
        )
        candidate = ledger.decorate(db, page)["correction_candidates"][0]
        accepted = ledger.append(
            db,
            page,
            target_kind="claim",
            target_id=candidate["id"],
            decision="accepted",
            reason="Matches new reading",
            reviewer="Reviewer",
            source_checked=True,
            reading_revision=correction["id"],
        )
        assert accepted["quote_origin"] == "corrected_reading"
        assert accepted["target_quote"] == "Order 1847 was not approved."
        assert ledger.decorate(db, page)["correction_candidates"][0]["review_status"] == "accepted"
        ledger.append(
            db,
            page,
            target_kind="reading",
            target_id=page["id"],
            decision="corrected",
            reason="Revised reading",
            reviewer="Reviewer",
            source_checked=True,
            correction="Order 1847 was approved.",
            expected_revision=correction["id"],
        )
        updated = ledger.decorate(db, page)
        assert updated["correction_candidates"][0]["review_status"] == "automatic_unreviewed"
        with pytest.raises(ValueError, match="does not belong"):
            ledger.append(
                db,
                page,
                target_kind="claim",
                target_id=candidate["id"],
                decision="accepted",
                reason="Stale",
                reviewer="Reviewer",
                source_checked=True,
                reading_revision=correction["id"],
            )
