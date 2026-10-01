"""Audit and fidelity contracts; these tests do not establish semantic accuracy."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_investigation_service import Answerer, Sources, completed

from evidencekg.investigations.evidence_contract import INSTRUCTIONS, validate_conclusions
from evidencekg.investigations.service import Investigations
from evidencekg.investigations.verifier import verify_package


@pytest.fixture
def service(tmp_path):
    value = Investigations(Sources(tmp_path / "workspace"), Answerer())
    yield value
    value.stop()


def conclusion(**changes):
    return dict(
        {
            "id": "c1",
            "text": "The source denies approval.",
            "status": "supported",
            "supporting": [{"segment_id": "seg1", "quote": "not approved"}],
            "contrary": [],
            "assumptions": [],
            "gaps": [],
        },
        **changes,
    )


def test_conclusion_fidelity_is_not_entailment():
    passages = [{"id": "seg1", "document_version_id": "doc1", "text": "The invoice was not approved."}]
    result = validate_conclusions({"answer": "x", "conclusions": [conclusion()]}, passages)
    assert result["conclusions"][0]["supporting"][0]["valid"]
    assert result["evidence_contract"]["semantic_support"] == "not_mechanically_verified"
    for change in [
        {"supporting": []},
        {"contrary": [{"segment_id": "seg1", "quote": "not approved"}]},
        {"supporting": [{"segment_id": "foreign", "quote": "not approved"}]},
        {"supporting": [{"segment_id": "seg1", "quote": "approved by Alice"}]},
        {"status": "conflicting"},
        {"status": "unresolved"},
    ]:
        with pytest.raises(ValueError):
            validate_conclusions({"conclusions": [conclusion(**change)]}, passages)
    with pytest.raises(ValueError, match="unique"):
        validate_conclusions({"conclusions": [conclusion(), conclusion()]}, passages)


def test_context_qualifications_and_omissions(service):
    snapshot = service.manager.investigation_snapshot("question")
    snapshot["partial"] = True
    snapshot["segments"]["seg1"]["knowledge"] = {
        "items": [{"condition": "only after inspection"}],
        "remaining": 2,
    }
    snapshot["segments"]["oversize"] = dict(snapshot["segments"]["seg1"], id="oversize", text="a" * 60001)
    snapshot["selected_segments"].append("oversize")
    ctx = service._context(snapshot, "what happened?", None)
    assert ctx["passages"][0]["knowledge"]["items"][0]["condition"] == "only after inspection"
    assert ctx["omitted_for_budget"] == ["oversize"]
    assert ctx["guidance"]["annotations_omitted"] == 2
    assert {m["kind"] for m in ctx["guidance"]["messages"]} >= {"coverage", "context", "budget"}
    assert ctx["instructions"] == INSTRUCTIONS


def test_review_ledger_reopen_export_and_retract(service, tmp_path):
    run = completed(service)
    first = service.review(
        run["id"], "segment", "seg1", "clarify", "Ambiguous attribution", "This is a source assertion."
    )
    assert first["actor"] == service.actor
    assert service.vault.events(run["id"])[-1]["payload"] == {"artifact_id": first["id"], "supersedes": None}
    assert "Ambiguous attribution" not in json.dumps(service.vault.events(run["id"]))
    reopened = Investigations(service.manager)
    assert reopened.reviews(run["id"])[0]["effective"]
    with pytest.raises(ValueError, match="reload"):
        reopened.review(run["id"], "segment", "seg1", "reject", "stale update")
    second = reopened.review(
        run["id"], "segment", "seg1", "retract", "I withdraw my assertion", supersedes=first["id"]
    )
    history = reopened.reviews(run["id"])
    assert [r["effective"] for r in history] == [False, False]
    assert history[1]["supersedes"] == first["id"]
    assert second["ledger_seq"] > first["ledger_seq"]
    assert verify_package(reopened.export(run["id"], tmp_path / "reviews.zip"))["integrity"]
    assert reopened.document(run["id"], "doc1")["passages"][0]["text"] == service.manager.text


def test_failed_ledger_commit_does_not_apply_review(service, monkeypatch):
    run = completed(service)
    original = service.vault.append_event

    def fail(kind, *args, **kwargs):
        if kind == "interpretation_reviewed":
            raise OSError("simulated disk failure")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(service.vault, "append_event", fail)
    with pytest.raises(OSError):
        service.review(run["id"], "segment", "seg1", "confirm", "Source inspected")
    assert service.reviews(run["id"]) == []
    assert any(a["kind"] == "interpretation_review" for a in service.vault.artifacts(run["id"]))


def test_foreign_review_and_erasure_fences(service):
    run = completed(service)
    with pytest.raises(ValueError, match="absent"):
        service.review(run["id"], "segment", "foreign", "confirm", "Source inspected")
    review = service.review(run["id"], "segment", "seg1", "confirm", "Source inspected")
    original = next(a["id"] for a in run["artifacts"] if a["kind"] == "source")
    preview = service.erasure_preview(run["id"], [original], "test erasure")
    assert review["id"] in preview["artifact_ids"]
    service.erase(run["id"], [original], "test erasure", preview["preview_hash"], preview["confirmation"])
    assert service.reviews(run["id"]) == []
    with pytest.raises(ValueError):
        service.review(run["id"], "segment", "seg1", "confirm", "late mutation")


def test_reviews_serialize_across_service_instances(service):
    run = completed(service)
    other = Investigations(service.manager)

    def apply(s):
        try:
            return s.review(run["id"], "segment", "seg1", "confirm", "concurrent review")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(apply, [service, other]))
    assert sum(isinstance(v, dict) for v in values) == 1
    assert len(service.reviews(run["id"])) == 1


def test_followup_receives_attributed_review_not_fact(service):
    run = completed(service)
    review = service.review(run["id"], "segment", "seg1", "reject", "Attribution disputed")
    child = service.create("Please reconsider", parent_id=run["id"], request_id="review-child")
    with service.db() as db:
        ctx = service._json(service._row(db, child["id"])["context_id"])
    assert ctx["human_reviews"][0]["id"] == review["id"]
    assert ctx["human_reviews"][0]["epistemic_status"] == "attributed_human_assertion_not_documentary_fact"


def test_new_unstructured_answers_fail_but_historical_results_remain_readable(service):
    with pytest.raises(ValueError, match="structured conclusions"):
        validate_conclusions({"answer": "Unsupported"}, [])
    run = completed(service)
    legacy = service._artifact({"answer": "Historical answer", "citations": []}, "result", run["id"])
    with service.db() as db:
        db.execute("UPDATE runs SET result_id=? WHERE id=?", (legacy["id"], run["id"]))
    assert service.detail(run["id"])["result"]["answer"] == "Historical answer"


def test_review_routes_require_auth_and_validate_targets(service, tmp_path):
    from fastapi.testclient import TestClient

    from evidencekg.app.api import create_app
    from evidencekg.app.config import AppConfig

    run = completed(service)
    home = service.manager.config.home
    token = home / "token"
    token.write_text("fictional-" * 8)
    token.chmod(0o600)
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("fixture")
    config = AppConfig(
        home=home, database_config=home / "unused-db", models=home / "models", token_file=token, ui_dist=ui
    )
    app = create_app(config, manager=service.manager, investigations=service, start_background=False)
    base = "/api/investigations/" + run["id"]
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get(base + "/review-targets").status_code == 401
        auth = {"Authorization": "Bearer " + config.token()}
        page = client.get(base + "/review-targets", headers=auth)
        assert page.status_code == 200
        assert any(t["target_kind"] == "conclusion" for t in page.json()["items"])
        body = dict(
            target_kind="conclusion", target_id="c1", decision="reject", reason="Unsupported conclusion"
        )
        assert client.post(base + "/reviews", json=body).status_code == 401
        saved = client.post(base + "/reviews", headers=auth, json=body)
        assert saved.status_code == 200, saved.text
        assert client.get(base, headers=auth).json()["reviews"][0]["ledger_seq"] == saved.json()["ledger_seq"]
        assert client.post(base + "/reviews", headers=auth, json=body).status_code == 422
        assert (
            client.post(base + "/reviews", headers=auth, json=body | {"target_id": "foreign"}).status_code
            == 422
        )


def test_erasure_intent_serializes_with_review_writes(service, monkeypatch):
    import threading

    run = completed(service)
    source = next(a["id"] for a in run["artifacts"] if a["kind"] == "source")
    preview = service.erasure_preview(run["id"], [source], "race fixture")
    entered, release = threading.Event(), threading.Event()
    original = service.erasure_preview

    def paused(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    monkeypatch.setattr(service, "erasure_preview", paused)
    other = Investigations(service.manager)
    with ThreadPoolExecutor(2) as pool:
        erase = pool.submit(
            service.erase,
            run["id"],
            [source],
            "race fixture",
            preview["preview_hash"],
            preview["confirmation"],
        )
        assert entered.wait(5)
        review = pool.submit(other.review, run["id"], "segment", "seg1", "confirm", "late review")
        assert not review.done()
        release.set()
        erase.result(timeout=5)
        with pytest.raises(ValueError, match="completed"):
            review.result(timeout=5)
    assert service.reviews(run["id"]) == []


def test_same_conclusion_ids_in_different_runs_do_not_overwrite_reviews(service):
    first = completed(service)
    review1 = service.review(first["id"], "conclusion", "c1", "reject", "First answer overstates certainty")
    second = service.create(
        "Consider the source again", parent_id=first["id"], request_id="second-reviewed-run"
    )
    service.execute(second["id"])
    review2 = service.review(second["id"], "conclusion", "c1", "confirm", "Second interpretation reviewed")
    third = service.create("Compare both reviews", parent_id=second["id"], request_id="third-reviewed-run")
    with service.db() as db:
        ctx = service._json(service._row(db, third["id"])["context_id"])
    assert {r["id"] for r in ctx["human_reviews"]} == {review1["id"], review2["id"]}
    assert {r["source_run_id"] for r in ctx["human_reviews"]} == {first["id"], second["id"]}
    assert len(service.detail(third["id"])["supplied_reviews"]) == 2


@pytest.mark.parametrize("decision", ["retract", "clarify"])
def test_followup_refreshes_ancestor_review_heads(service, decision):
    first = completed(service)
    original = service.review(
        first["id"], "segment", "seg1", "clarify", "Initial interpretation", "Alice is the approver"
    )
    second = service.create("Consider this review", parent_id=first["id"], request_id="review-parent")
    service.execute(second["id"])
    updated = service.review(
        first["id"],
        "segment",
        "seg1",
        decision,
        "New evidence changes the interpretation",
        "Identity is unresolved" if decision == "clarify" else "",
        supersedes=original["id"],
    )
    third = service.create("Reassess", parent_id=second["id"], request_id="review-grandchild")
    supplied = service.detail(third["id"])["supplied_reviews"]
    assert [r["id"] for r in supplied] == ([updated["id"]] if decision == "clarify" else [])
    # Historical inputs and the entire attributed audit chain remain intact.
    assert service.detail(second["id"])["supplied_reviews"][0]["id"] == original["id"]
    assert [r["id"] for r in service.reviews(first["id"])] == [original["id"], updated["id"]]


def test_gap_metadata_is_erased_across_unrelated_runs_and_cannot_be_recaptured(service, monkeypatch):
    from evidencekg.app.manager import NotReady

    original_snapshot = service.manager.investigation_snapshot
    private_path = "/private/Alice-medical-record.pdf"

    def private_snapshot(*args, **kwargs):
        value = original_snapshot(*args, **kwargs)
        value["manifest"]["documents"][0]["path"] = private_path
        return value

    monkeypatch.setattr(service.manager, "investigation_snapshot", private_snapshot)
    first = completed(service)
    source = next(a["id"] for a in first["artifacts"] if a["kind"] == "source")

    def unrelated_snapshot(*args, **kwargs):
        value = original_snapshot(*args, **kwargs)
        value["manifest"]["documents"] = [
            {"document_version_id": "doc2", "path": "/public/other.txt", "status": "ready"}
        ]
        value["segments"]["seg1"]["document_version_id"] = "doc2"
        value["originals"] = {"doc2": b"Another invoice was not approved."}
        value["collection_gaps"] = [
            {
                "document_version_id": "doc1",
                "path": private_path,
                "status": "partial",
                "warnings": ["Private extraction detail"],
            }
        ]
        return value

    monkeypatch.setattr(service.manager, "investigation_snapshot", unrelated_snapshot)
    second = completed(service, request="unrelated-gap-copy")
    assert second["guidance"]["document_gaps"][0]["path"] == private_path
    with service.db() as db:
        row = service._row(db, second["id"])
        copied_artifacts = [row["snapshot_id"], row["context_id"], row["result_id"]]
    preview = service.erasure_preview(first["id"], [source], "Erase private source")
    assert second["id"] in preview["affected_runs"]
    assert set(copied_artifacts) <= set(preview["artifact_ids"])
    service.erase(
        first["id"], [source], "Erase private source", preview["preview_hash"], preview["confirmation"]
    )
    assert all(service.vault.artifact(key)["deleted"] for key in copied_artifacts)
    with pytest.raises(NotReady, match="erased evidence"):
        service.create("Unrelated query", request_id="restricted-gap-copy")


@pytest.mark.parametrize("collection_total", [1, 101])
def test_gap_guidance_merges_retained_documents_and_preserves_unknown_totals(collection_total):
    from evidencekg.investigations.evidence_contract import coverage_guidance

    collection = {
        "document_version_id": "doc1",
        "path": "collection.pdf",
        "status": "partial",
        "warnings": ["OCR"],
    }
    upload = {
        "document_version_id": "upload-1",
        "path": "uploaded.pdf",
        "status": "partial",
        "warnings": ["Visual content"],
    }
    inherited = {
        "document_version_id": "ancestor-1",
        "path": "earlier.pdf",
        "status": "partial",
        "warnings": ["Unread pages"],
    }
    guidance = coverage_guidance(
        {
            "collection_gaps": [collection],
            "collection_gaps_total": collection_total,
            "manifest": {"documents": [collection, upload, inherited]},
        },
        [],
        [],
    )
    assert [g["path"] for g in guidance["document_gaps"]] == ["collection.pdf", "uploaded.pdf", "earlier.pdf"]
    assert guidance["document_gaps_total"] == max(3, collection_total)
    assert guidance["document_gaps_total_is_lower_bound"] == (collection_total > 1)
    assert "extraction" in {m["kind"] for m in guidance["messages"]}
    empty_collection = coverage_guidance(
        {
            "collection_gaps": [],
            "collection_gaps_total": 0,
            "manifest": {"documents": [upload]},
        },
        [],
        [],
    )
    assert empty_collection["document_gaps"][0]["warnings"] == ["Visual content"]
    assert empty_collection["document_gaps_total"] == 1
