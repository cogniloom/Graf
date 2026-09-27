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
        home=service.home.parent, source=lambda value: source,
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
