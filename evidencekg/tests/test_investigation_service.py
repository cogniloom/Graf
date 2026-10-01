"""Real local session/vault/API storage with deterministic provider fixtures.

These tests prove local contracts, not hosted provider or legal correctness.
"""

import copy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from evidencekg.app.manager import NotReady
from evidencekg.investigations.service import Investigations
from evidencekg.investigations.verifier import verify_package


class Sources:
    id = "synthetic-workspace"

    def __init__(self, home):
        home.mkdir(mode=0o700)
        self.config = SimpleNamespace(home=home)
        self.partial = False
        self.text = "The invoice was not approved."

    def investigation_snapshot(self, question, allow_partial=False):
        if self.partial and not allow_partial:
            raise NotReady("Explicit partial consent required")
        return copy.deepcopy(
            {
                "snapshot_id": "synthetic-snapshot",
                "revision": 1,
                "published_revision": 1,
                "partial": self.partial,
                "retrieval": "test-fixture",
                "known_source_files": 1,
                "manifest": {
                    "documents": [{"document_version_id": "doc1", "path": "invoice.txt", "status": "ready"}]
                },
                "segments": {
                    "seg1": {
                        "id": "seg1",
                        "document_version_id": "doc1",
                        "text": self.text,
                        "locators": {"line": 1},
                    }
                },
                "links": [],
                "originals": {"doc1": self.text.encode()},
                "selected_segments": ["seg1"],
            }
        )


class Answerer:
    def execute(self, prompt, cwd, on_event, cancel, **kwargs):
        context = json.loads(prompt)
        on_event({"type": "message", "text": "Reviewing supplied evidence", "time": "synthetic"})
        return {
            "answer": "Approval was not given.",
            "conclusions": [{"id": "c1", "text": "The source denies approval.", "status": "supported",
                             "supporting": [{"segment_id": "seg1", "quote": "not approved"}],
                             "contrary": [], "assumptions": [], "gaps": []}],
            "citations": [
                {"segment_id": "seg1", "quote": "not approved"},
                {"segment_id": "seg1", "quote": "invented quotation"},
            ],
            "documents": [
                {
                    "name": "report.md",
                    "media_type": "text/markdown",
                    "content": context["passages"][0]["text"],
                }
            ],
        }


@pytest.fixture
def service(tmp_path):
    value = Investigations(Sources(tmp_path / "workspace"), Answerer())
    yield value
    value.stop()


def completed(service, request="request-one"):
    run = service.create("Was the invoice approved?", request_id=request)
    service.execute(run["id"])
    result = service.detail(run["id"])
    assert result["state"] == "completed", result.get("error")
    return result


def test_durable_snapshot_outputs_coverage_and_package(service, tmp_path):
    run = service.create("Was the invoice approved?", request_id="same-request")
    service.manager.text = "Changed after the run was accepted."
    service.execute(run["id"])
    detail = service.detail(run["id"])
    assert detail["result"]["citations"][0]["valid"] is True
    assert detail["result"]["citations"][1]["valid"] is False
    assert detail["coverage"] == {
        "supplied_passages": 1,
        "cited_passages": 1,
        "total_documents": 1,
        "omitted_for_budget": [],
    }
    generated = next(a for a in detail["artifacts"] if a.get("kind") == "generated_document")
    assert service.vault.read_artifact(generated["id"]) == b"The invoice was not approved."
    package = service.export(run["id"], tmp_path / "package.zip")
    assert verify_package(package)["integrity"]
    assert not verify_package(package)["trusted"]
    reopened = Investigations(service.manager, Answerer())
    assert reopened.detail(run["id"])["result"] == detail["result"]


def test_explicit_partial_and_idempotent_request(service):
    service.manager.partial = True
    with pytest.raises(NotReady):
        service.create("Question")
    run = service.create("Question", allow_partial=True, request_id="idempotent")
    assert run["partial"]
    assert service.create("Question", allow_partial=True, request_id="idempotent")["id"] == run["id"]
    with pytest.raises(ValueError, match="different input"):
        service.create("Different", allow_partial=True, request_id="idempotent")


def test_followup_is_linked_new_run_and_conservative_erasure_removes_copies(service):
    first = completed(service)
    child = service.create("Write a follow-up", parent_id=first["id"], request_id="child")
    service.execute(child["id"])
    assert child["session_id"] == first["session_id"]
    target = next(a["id"] for a in first["artifacts"] if a.get("kind") == "source")
    preview = service.erasure_preview(first["id"], [target], "Synthetic order")
    assert set(preview["affected_runs"]) == {first["id"], child["id"]}
    with pytest.raises(ValueError, match="fresh impact"):
        service.erase(first["id"], [target], "Synthetic order", "wrong", preview["confirmation"])
    receipt = service.erase(
        first["id"], [target], "Synthetic order", preview["preview_hash"], preview["confirmation"]
    )
    assert receipt["external_obligations"]
    assert service.detail(first["id"])["state"] == "erased"
    assert service.detail(child["id"])["state"] == "erased"
    for artifact in service.vault.artifacts():
        if not artifact["deleted"]:
            assert b"The invoice was not approved." not in service.vault.read_artifact(artifact["id"])
    with pytest.raises(NotReady, match="erased evidence"):
        service.create("Try to reuse erased evidence", request_id="new-run")


def test_output_erasure_removes_raw_result_copy(service):
    run = completed(service)
    target = next(a["id"] for a in run["artifacts"] if a.get("kind") == "generated_document")
    preview = service.erasure_preview(run["id"], [target], "Remove generated report")
    service.erase(
        run["id"], [target], "Remove generated report", preview["preview_hash"], preview["confirmation"]
    )
    assert not service.detail(run["id"])["result"]
    assert any(a.get("kind") == "source" and not a["deleted"] for a in service.vault.artifacts(run["id"]))


def test_provider_failure_is_durable_and_not_retried(service):
    class Broken:
        def execute(self, prompt, cwd, on_event, cancel, **kwargs):
            on_event({"type": "partial", "text": "Some output"})
            raise RuntimeError("Synthetic provider disconnect")

    service.adapter = Broken()
    run = service.create("Question")
    service.execute(run["id"])
    detail = service.detail(run["id"])
    assert detail["state"] == "failed"
    assert "disconnect" in detail["error"]["message"]
    assert detail["activity"]
    before = len(service.vault.events())
    service.execute(run["id"])
    assert len(service.vault.events()) == before


def test_cancel_releases_executor_and_no_late_success(service):
    entered = threading.Event()

    class Waiting:
        def execute(self, prompt, cwd, on_event, cancel, **kwargs):
            entered.set()
            assert cancel.wait(5)
            return {"answer": "Too late", "citations": [], "documents": []}

    service.adapter = Waiting()
    run = service.create("Question")
    service.start()
    assert entered.wait(5)
    service.cancel(run["id"])
    deadline = time.monotonic() + 5
    while service.detail(run["id"])["state"] == "cancelling" and time.monotonic() < deadline:
        time.sleep(0.02)
    detail = service.detail(run["id"])
    assert detail["state"] == "cancelled"
    assert not detail["result"]


def test_restart_marks_unknown_inflight_run_interrupted(service):
    run = service.create("Question")
    with service.db() as db:
        db.execute("UPDATE runs SET state='running' WHERE id=?", (run["id"],))
    service.start()
    assert service.detail(run["id"])["state"] == "interrupted"


def test_source_restrictions_apply_to_watcher_inventory(service, tmp_path):
    from evidencekg.app.files import inventory

    run = completed(service)
    target = next(a["id"] for a in run["artifacts"] if a.get("kind") == "source")
    preview = service.erasure_preview(run["id"], [target], "Synthetic order")
    service.erase(run["id"], [target], "Synthetic order", preview["preview_hash"], preview["confirmation"])
    source = tmp_path / "originals"
    source.mkdir()
    (source / "invoice.txt").write_text(service.manager.text)
    (source / "other.txt").write_text("Unrelated retained source")
    config = SimpleNamespace(
        home=service.home.parent,
        source=lambda value: source,
        private_source=lambda path: path.is_relative_to(service.home.parent),
    )
    items = inventory(config, {"path": str(source), "kind": "directory"})
    assert list(items) == ["other.txt"]


def test_authorized_erasure_recovers_after_storage_failure(service, monkeypatch):
    detail = completed(service)
    target = next(a["id"] for a in detail["artifacts"] if a.get("kind") == "source")
    preview = service.erasure_preview(detail["id"], [target], "Synthetic deletion order")
    original = service.vault.erase

    def crash(*args, **kwargs):
        raise OSError("Synthetic interrupted storage operation")

    monkeypatch.setattr(service.vault, "erase", crash)
    with pytest.raises(OSError):
        service.erase(
            detail["id"],
            [target],
            "Synthetic deletion order",
            preview["preview_hash"],
            preview["confirmation"],
        )
    assert service.detail(detail["id"])["state"] == "deleting"
    assert service.detail(detail["id"])["result"] == {}
    assert service.detail(detail["id"])["artifacts"] == []
    assert service.list()["items"][0]["prompt"] == "[content erased]"
    with pytest.raises(ValueError):
        service.annotation(detail["id"], "A copy of erased evidence")
    with pytest.raises(NotReady):
        service.document(detail["id"], "doc1")
    with pytest.raises(NotReady):
        service.export(detail["id"], service.home / "blocked.zip")
    monkeypatch.setattr(service.vault, "erase", original)
    service.recover_deletions()
    assert service.detail(detail["id"])["state"] == "erased"
    assert service.vault.artifact(target)["deleted"]
    service.recover_deletions()  # Completed intents are not executed twice.
    with service.db() as db:
        assert db.execute("SELECT state FROM deletions").fetchone()[0] == "local_erasure_complete"


def test_orphan_export_removed_before_erasure(service):
    detail = completed(service)
    folder = service.home / "graf-export-crashed"
    folder.mkdir()
    service.export(detail["id"], folder / "evidence.zip")
    target = next(a["id"] for a in detail["artifacts"] if a.get("kind") == "source")
    preview = service.erasure_preview(detail["id"], [target], "Synthetic order")
    service.erase(detail["id"], [target], "Synthetic order", preview["preview_hash"], preview["confirmation"])
    assert not folder.exists()


def test_second_server_cannot_start_on_same_workspace(service):
    service.start()
    other = Investigations(service.manager, Answerer())
    with pytest.raises(RuntimeError, match="Another Graf"):
        other.start()


def upload(name, text):
    import base64

    return {"name": name, "data": base64.b64encode(text.encode()).decode()}


def test_acceptance_is_fast_and_history_cancel_remain_responsive(service):
    entered, release = threading.Event(), threading.Event()
    original = service.manager.investigation_snapshot

    def slow(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    service.manager.investigation_snapshot = slow
    run = service.create("Question", request_id="queued-request", defer=True)
    assert run["state"] == "preparing"
    assert not entered.is_set()  # Acceptance never invokes retrieval.
    service.start()
    # start() marks unfinished preparations interrupted on restart; queue after ownership starts.
    run = service.create("Question", request_id="running-request", defer=True)
    try:
        assert entered.wait(2)
        assert service.list()["items"]
        assert service.detail(run["id"])["state"] == "preparing"
        assert service.create("Question", request_id="running-request", defer=True)["id"] == run["id"]
        assert service.cancel(run["id"])["state"] == "cancelling"
    finally:
        release.set()
        service.stop()
    assert service.detail(run["id"])["state"] == "cancelled"


def test_preparation_error_is_retained_without_killing_queue(service):
    def broken(*args):
        raise ValueError("Source unavailable: retry after indexing")

    service.manager.investigation_snapshot = broken
    service.start()
    run = service.create("Question", defer=True)
    for _ in range(100):
        detail = service.detail(run["id"])
        if detail["state"] == "failed":
            break
        time.sleep(0.01)
    assert detail["state"] == "failed"
    assert "Source unavailable" in detail["error"]["message"]
    assert service.worker.is_alive()


def test_attachment_referenced_in_questions_and_followup_preserves_source(service, tmp_path):
    class Clarifying:
        def execute(self, prompt, *args, **kwargs):
            ctx = json.loads(prompt)
            attachment = next(p for p in ctx["passages"] if p.get("attachment"))
            assert attachment["source_path"] == "memo.txt"
            assert attachment["text"] == "Release marker: BLUE-742"
            if not ctx.get("previous_result"):
                return {
                    "answer": "Which format?",
                    "conclusions": [],
                    "citations": [],
                    "documents": [],
                    "questions": [
                        {"id": "format", "question": "Which format?", "options": ["Short", "Long"], "kind": "scope", "reason": "The requested report length is unspecified."}
                    ],
                }
            assert ctx["previous_result"]["questions"][0]["id"] == "format"
            assert ctx["previous_prompt"] == "Read memo.txt"
            return {
                "answer": "BLUE-742",
                "conclusions": [{"id": "marker", "text": "The supplied marker is BLUE-742.", "status": "supported",
                                 "supporting": [{"segment_id": attachment["id"], "quote": "BLUE-742"}],
                                 "contrary": [], "assumptions": [], "gaps": []}],
                "questions": [],
                "documents": [],
                "citations": [{"segment_id": attachment["id"], "quote": "BLUE-742"}],
            }

    service.adapter = Clarifying()
    first = service.create("Read memo.txt", attachments=[upload("memo.txt", "Release marker: BLUE-742")])
    service.execute(first["id"])
    assert service.detail(first["id"])["state"] == "awaiting_input"
    child = service.create("Short", parent_id=first["id"])
    service.execute(child["id"])
    detail = service.detail(child["id"])
    assert detail["state"] == "completed", detail["error"]
    assert detail["result"]["citations"][0]["valid"]
    evidence = next(e for e in detail["evidence"] if e["path"] == "memo.txt")
    assert service.vault.read_artifact(evidence["artifact_id"]) == b"Release marker: BLUE-742"
    assert verify_package(service.export(child["id"], tmp_path / "followup.zip"))["integrity"]


def test_upload_limits_idempotency_and_erasure(service):
    a = upload("memo.txt", "Attachment content")
    run = service.create("Read memo.txt", attachments=[a], request_id="upload-retry", defer=True)
    assert (
        service.create("Read memo.txt", attachments=[a], request_id="upload-retry", defer=True)["id"]
        == run["id"]
    )
    with pytest.raises(ValueError, match="different input"):
        service.create(
            "Read memo.txt",
            attachments=[upload("memo.txt", "Changed")],
            request_id="upload-retry",
            defer=True,
        )
    for invalid in [upload("../memo.txt", "x"), upload("memo.exe", "x"), {"name": "memo.txt", "data": "%%%"}]:
        with pytest.raises(ValueError):
            service.create("Read", attachments=[invalid], defer=True)
    target = next(a["id"] for a in service.vault.artifacts(run["id"]) if a["kind"] == "source")
    preview = service.erasure_preview(run["id"], [target], "Test deletion")
    assert preview["active_runs"] == [run["id"]]
    service.cancel(run["id"])
    preview = service.erasure_preview(run["id"], [target], "Test deletion")
    service.erase(run["id"], [target], "Test deletion", preview["preview_hash"], preview["confirmation"])
    with pytest.raises(NotReady, match="erased evidence"):
        service.create("Read", attachments=[a], defer=True)


def test_oversize_extracted_text_fails_without_silent_truncation(service):
    with pytest.raises(ValueError, match="nothing was truncated"):
        service.create("Read", attachments=[upload("large.txt", "X" * 48001)])


@pytest.mark.parametrize("suffix", ["txt", "md", "csv", "json", "pdf", "docx"])
def test_supported_upload_formats_retain_original_and_supply_text(service, suffix):
    import base64
    import io

    text = "File marker BLUE-742"
    data = text.encode()
    if suffix == "pdf":
        from reportlab.pdfgen.canvas import Canvas

        stream = io.BytesIO()
        canvas = Canvas(stream)
        canvas.drawString(50, 700, text)
        canvas.save()
        data = stream.getvalue()
    elif suffix == "docx":
        from docx import Document

        stream = io.BytesIO()
        document = Document()
        document.add_paragraph(text)
        document.save(stream)
        data = stream.getvalue()
    run = service.create(
        "Read the attachment",
        include_collection=False,
        attachments=[{"name": "memo." + suffix, "data": base64.b64encode(data).decode()}],
    )
    with service.db() as db:
        row = service._row(db, run["id"])
        context = service._json(row["context_id"])
    assert text in "\n".join(p["text"] for p in context["passages"])
    source = service.detail(run["id"])["evidence"][0]
    assert source["path"] == "memo." + suffix
    assert service.vault.read_artifact(source["artifact_id"]) == data


def test_restricted_second_upload_writes_no_orphan_artifacts(service):
    from evidencekg.db import sha

    with service.db() as db:
        db.execute("INSERT INTO restrictions VALUES(?,?)", (sha(b"blocked"), "restriction-test"))
    before = service.vault.artifacts()
    with pytest.raises(NotReady, match="erased evidence"):
        service.create(
            "Read files",
            attachments=[upload("first.txt", "retained secret"), upload("blocked.txt", "blocked")],
            defer=True,
        )
    assert service.list()["items"] == []
    assert service.vault.artifacts() == before


def test_storage_failure_keeps_partial_uploads_reachable_in_failed_run(service, monkeypatch):
    original = service._artifact

    def fail_second(content, kind, *args, **kwargs):
        if kind == "source" and kwargs.get("name") == "second.txt":
            raise OSError("Synthetic storage failure")
        return original(content, kind, *args, **kwargs)

    monkeypatch.setattr(service, "_artifact", fail_second)
    with pytest.raises(OSError, match="storage failure"):
        service.create(
            "Read files",
            attachments=[upload("first.txt", "retained content"), upload("second.txt", "second content")],
            defer=True,
        )
    run = service.list()["items"][0]
    assert run["state"] == "failed"
    detail = service.detail(run["id"])
    assert "fully retained" in detail["error"]["message"]
    retained = next(a for a in detail["artifacts"] if a["kind"] == "source")
    assert retained["run_id"] == run["id"]
    preview = service.erasure_preview(run["id"], [retained["id"]], "Remove failed upload")
    assert preview["affected_runs"] == [run["id"]]


def test_duplicate_question_ids_never_become_awaiting_input(service):
    class InvalidQuestions:
        def execute(self, *args, **kwargs):
            return {
                "answer": "",
                "citations": [],
                "documents": [],
                "questions": [
                    {"id": "same", "question": "First?", "options": []},
                    {"id": "same", "question": "Second?", "options": []},
                ],
            }

    service.adapter = InvalidQuestions()
    run = service.create("Clarify")
    service.execute(run["id"])
    detail = service.detail(run["id"])
    assert detail["state"] == "failed"
    assert "distinct nonempty IDs" in detail["error"]["message"]
