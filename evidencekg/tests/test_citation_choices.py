import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest
from test_assurance import result_for, setup, submit

from evidencekg.citation_choices import SelectedCitationResult, choices, resolve, selection_schema
from evidencekg.db import dump, sha
from evidencekg.reports import verify
from evidencekg.validation import schema_for_payload
from evidencekg.worker_adapters.cli import ExistingLawcaseCodex


def test_choice_schema_scope_and_exact_expansion(vault):
    _, queue, rid = setup(vault)
    task = queue.next_review_task(rid, "test", kinds=["source"])
    payload = task["payload"]
    catalog = choices(payload)
    cid = next(iter(catalog))
    raw = result_for(task)
    raw["findings"] = [
        {
            "assertion": "Conditional statement",
            "epistemic_status": "observation",
            "sources": [{"citation_id": cid}],
            "uncertainty": "",
        }
    ]
    schema = selection_schema(schema_for_payload(payload), catalog)
    jsonschema.Draft202012Validator(schema).validate(raw)
    expanded = resolve(raw, payload)
    assert expanded["findings"][0]["sources"] == [catalog[cid]]
    assert raw["findings"][0]["sources"] == [{"citation_id": cid}]
    with pytest.raises(ValueError):
        resolve({"sources": [{"citation_id": "Q-forged"}]}, payload)
    with pytest.raises(ValueError):
        resolve({"sources": [{"citation_id": cid, "quote": "invented"}]}, payload)
    other = queue.next_review_task(rid, "test", kinds=["source"])
    with pytest.raises(ValueError):
        resolve(raw, other["payload"])


def test_raw_choice_receipt_is_preserved_idempotent_and_verified(vault):
    store, queue, rid = setup(vault)
    task = queue.next_review_task(rid, "test", kinds=["source"])
    raw = result_for(task)
    cid = next(iter(choices(task["payload"])))
    raw["findings"] = [
        {
            "assertion": "Source says this",
            "epistemic_status": "observation",
            "sources": [{"citation_id": cid}],
            "uncertainty": "",
        }
    ]
    result = SelectedCitationResult(resolve(raw, task["payload"]), raw)
    submit(queue, task, result)
    assert submit(queue, task, result)["idempotent"]
    forged = SelectedCitationResult(dict(result), {"citation_id": "Q-forged"})
    with pytest.raises(ValueError, match="duplicate citation-selection"):
        submit(queue, task, forged)
    events = store.rows("select * from audit_events where event_type='citation_choices_resolved'")
    assert len(events) == 1
    receipt = json.loads(events[0]["payload_json"])
    assert json.loads(store.get(receipt["raw_output_sha"])) == raw
    assert receipt["resolved_output_sha"] == sha(dump(result))
    assert verify(store)["ok"]
    with store.write():
        store.audit(
            "citation_choices_resolved", {**receipt, "resolved_output_sha": receipt["raw_output_sha"]}, rid
        )
    assert any("Citation selection receipt" in e for e in verify(store)["errors"])


@pytest.mark.parametrize("malformed", [True, False])
def test_bad_raw_lineage_is_durably_rejected_without_poisoning_integrity(vault, malformed):
    store, queue, rid = setup(vault)
    task = queue.next_review_task(rid, "test", kinds=["source"])
    raw = result_for(task)
    expanded = resolve(raw, task["payload"])
    if malformed:
        raw = {"citation_id": "Q-forged"}
    else:
        expanded["uncertainty"] = "changed after model output"
    with pytest.raises(ValueError):
        submit(queue, task, SelectedCitationResult(expanded, raw))
    attempt = store.one("SELECT * FROM task_attempts WHERE id=?", (task["lease_id"],))
    assert attempt["validation_status"] == "invalid" and attempt["output_sha"]
    assert store.one("SELECT state FROM review_tasks WHERE id=?", (task["task_id"],))["state"] == "pending"
    assert store.rows("SELECT * FROM audit_events WHERE event_type='citation_choices_rejected'")
    assert verify(store)["ok"]


def test_subscription_adapter_selects_choices_and_checks_actual_budget(vault, tmp_path, monkeypatch):
    _, queue, rid = setup(vault)
    task = queue.next_review_task(rid, "test", kinds=["source"])
    project = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project))
    import lawcase_worker

    captured = []

    class Fake:
        def __init__(self, state, schemas, **kwargs):
            self.schemas = schemas
            self.trusted_instructions = kwargs["trusted_instructions"]

        def call(self, stage, packet):
            captured.append(self.trusted_instructions[stage])
            raw = result_for(packet)
            cid = next(iter(choices(packet["payload"])))
            raw["findings"] = [
                {
                    "assertion": "Observed",
                    "epistemic_status": "observation",
                    "sources": [{"citation_id": cid}],
                    "uncertainty": "",
                }
            ]
            jsonschema.Draft202012Validator(self.schemas[stage]).validate(raw)
            return raw

    monkeypatch.setattr(lawcase_worker, "CodexWorker", Fake)
    worker = ExistingLawcaseCodex(project, tmp_path)
    result = worker.call(task)
    assert isinstance(result, SelectedCitationResult)
    assert result.raw_citation_output["findings"][0]["sources"][0].keys() == {"citation_id"}
    worker.call(task)
    assert captured[0] == captured[1]
    oversized = copy.deepcopy(task)
    oversized["payload"]["max_input_bytes"] = 1
    with pytest.raises(ValueError, match="budget"):
        worker.call(oversized)
    assert len(captured) == 2


def test_choice_covers_a_marker_crossing_catalog_boundary(vault):
    from evidencekg.ingest import ingest
    from evidencekg.review_queue import Queue

    root, store = vault
    (root / "long.txt").write_text("x" * 389 + " subject to approval.")
    queue = Queue(store)
    rid = queue.start_review(ingest(store), "Read conditions", enhanced=True)["run_id"]
    task = queue.next_review_task(rid, "test", kinds=["critical_wording"])
    catalog = choices(task["payload"])
    assert task["payload"]["segments"][0]["citation_extra_spans"]
    for marker in task["payload"]["descriptor"]["risk_markers"]:
        assert any(r["start"] <= marker["start"] and r["end"] >= marker["end"] for r in catalog.values())
