import sys
from pathlib import Path

from evidencekg.validation import RESULT_SCHEMA
from evidencekg.worker_adapters.cli import ExistingLawcaseCodex


def test_codex_schema_subset_keeps_full_local_validation(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project))
    import lawcase_worker

    captured = {}

    class FakeWorker:
        def __init__(self, state, schemas, **kwargs):
            captured.update(schemas)

    monkeypatch.setattr(lawcase_worker, "CodexWorker", FakeWorker)
    ExistingLawcaseCodex(project, tmp_path)
    assert RESULT_SCHEMA["properties"]["unresolved_questions"]["uniqueItems"]
    assert "uniqueItems" not in captured["evidence"]["properties"]["unresolved_questions"]
    assert (
        captured["evidence"]["properties"]["unresolved_questions"]["maxItems"]
        == RESULT_SCHEMA["properties"]["unresolved_questions"]["maxItems"]
    )


def test_codex_enhanced_stage_schema_is_selected_without_provider_calls(tmp_path, monkeypatch):
    from evidencekg.validation import STAGE_SCHEMAS

    project = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project))
    import lawcase_worker

    captured = {}

    class FakeWorker:
        def __init__(self, state, schemas, **kwargs):
            captured["schemas"] = schemas
            self.schemas = schemas
            captured["instructions"] = kwargs["trusted_instructions"]
            self.trusted_instructions = kwargs["trusted_instructions"]

        def call(self, stage, task):
            captured["stage"] = stage
            return task

    monkeypatch.setattr(lawcase_worker, "CodexWorker", FakeWorker)
    worker = ExistingLawcaseCodex(project, tmp_path)
    for kind in STAGE_SCHEMAS:
        worker.call({"input_sha": "hash", "payload": {"assurance_version": "v1", "kind": kind}})
        assert captured["stage"] == kind
        assert "observations" in captured["schemas"][kind]["properties"]
        assert "Critical wording review is independent" in captured["instructions"][kind]
    worker.call({"input_sha": "hash", "payload": {"kind": "source"}})
    assert captured["stage"] == "evidence"
    assert STAGE_SCHEMAS["source"]["properties"]["observations"]["items"]["properties"]["issue_keys"][
        "uniqueItems"
    ]
    assert captured["schemas"]["source_reconciliation"]["properties"]["wording_checks"]["maxItems"] == 0
    worker.call(
        {
            "task_id": "T-current",
            "input_sha": "hash-current",
            "payload": {
                "assurance_version": "v1",
                "kind": "source_reconciliation",
                "segments": [{"id": "S-current", "extraction_id": "X-current"}],
                "descriptor": {"targets": [{"id": "current-target"}]},
            },
        }
    )
    schema = captured["schemas"]["source_reconciliation"]
    assert schema["properties"]["assessments"]["items"]["properties"]["target_id"]["enum"] == [
        "current-target"
    ]
    assert schema["properties"]["task_id"]["enum"] == ["T-current"]
