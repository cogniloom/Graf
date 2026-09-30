import json
import sys

import pytest
from test_core import drain, empty_result

from evidencekg.config import initialize
from evidencekg.db import dump
from evidencekg.ingest import ingest
from evidencekg.review_queue import Queue
from evidencekg.runner import run as run_worker
from evidencekg.validation import validate_result
from evidencekg.worker_adapters.cli import CLIWorker, WorkerOutputError


def review(vault, **kwargs):
    root, store = vault
    (root / "x.txt").write_text("Original deadline ONLY")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review the deadline", **kwargs)["run_id"]
    return store, queue, run_id


def submit(queue, task, result):
    return queue.submit_review_result(
        task["run_id"], task["task_id"], task["lease_id"], task["input_sha"], result
    )


class SyntheticWorker:
    model = "synthetic-v1"
    effort = "medium"

    def __init__(self, error=False):
        self.calls = 0
        self.error = error

    def call(self, task):
        self.calls += 1
        if self.error:
            raise RuntimeError("synthetic failure")
        return empty_result(task)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(extra="unexpected"),
        lambda r: r.pop("uncertainty"),
        lambda r: r.update(task_id="wrong"),
        lambda r: r.update(input_sha="wrong"),
        lambda r: r.update(modality_reviewed=True),
        lambda r: r.update(unresolved_questions=[" "]),
        lambda r: r.update(unresolved_questions=["x" * 110000]),
        lambda r: r.update(uncertainty="x" * 1000001),
        lambda r: r.update(uncertainty=float("nan")),
        lambda r: r.update(uncertainty=object()),
    ],
)
def test_rejections_keep_attempt_and_retry_budget(vault, mutation):
    store, queue, run_id = review(vault)
    for _ in range(store.config()["max_attempts"]):
        task = queue.next_review_task(run_id, "synthetic")
        result = empty_result(task)
        mutation(result)
        with pytest.raises(ValueError):
            submit(queue, task, result)
        attempt = store.one("SELECT * FROM task_attempts WHERE id=?", (task["lease_id"],))
        assert attempt["validation_status"] == "invalid"
        assert attempt["output_sha"] and store.get(attempt["output_sha"])
        assert attempt["finished_at"] and json.loads(attempt["error_json"])["error"]
    assert queue.next_review_task(run_id, "synthetic") is None
    assert queue.review_status(run_id)["state"] == "failed_unresolved"


def test_worker_errors_cannot_bypass_cap_by_resuming(vault):
    store, queue, run_id = review(vault)
    worker = SyntheticWorker(error=True)
    for _ in range(8):
        status = run_worker(store, run_id, worker)
    assert worker.calls == store.config()["max_attempts"]
    assert status["state"] == "failed_unresolved"
    assert len(store.rows("SELECT * FROM task_attempts")) == worker.calls
    assert all(a["validation_status"] == "worker_error" for a in store.rows("SELECT * FROM task_attempts"))


def test_expired_submission_and_expired_leases_are_finite(vault):
    store, queue, run_id = review(vault)
    task = queue.next_review_task(run_id, "synthetic")
    store.db.execute("UPDATE review_tasks SET lease_until=0 WHERE id=?", (task["task_id"],))
    with pytest.raises(ValueError, match="Stale"):
        submit(queue, task, empty_result(task))
    attempt = store.one("SELECT * FROM task_attempts WHERE id=?", (task["lease_id"],))
    assert attempt["validation_status"] == "expired" and attempt["output_sha"]
    for _ in range(store.config()["max_attempts"] - 1):
        current = queue.next_review_task(run_id, "synthetic")
        store.db.execute("UPDATE review_tasks SET lease_until=0 WHERE id=?", (current["task_id"],))
    assert queue.next_review_task(run_id, "synthetic") is None
    assert queue.review_status(run_id)["failed_tasks"] == 1
    with pytest.raises(ValueError, match="Stale"):
        submit(queue, task, empty_result(task))


def test_terminal_resume_and_idempotency_do_not_call_again(vault):
    store, queue, run_id = review(vault)
    worker = SyntheticWorker()
    completed = run_worker(store, run_id, worker)
    calls = worker.calls
    assert completed["state"] == "scheduled_work_complete"
    assert run_worker(store, run_id, worker) == completed
    assert worker.calls == calls
    task = store.one("SELECT * FROM review_tasks WHERE state='validated' LIMIT 1")
    result = json.loads(store.get(task["result_sha"]))
    assert queue.submit_review_result(
        run_id, task["id"], task["lease_id"], task["input_manifest_sha"], result
    )["idempotent"]
    result["uncertainty"] = "changed"
    with pytest.raises(ValueError, match="Conflicting"):
        queue.submit_review_result(run_id, task["id"], task["lease_id"], task["input_manifest_sha"], result)


@pytest.mark.parametrize("field,value", [("model", "another"), ("effort", "high")])
def test_resume_freezes_worker_model_and_effort(vault, field, value):
    store, queue, run_id = review(vault)
    worker = SyntheticWorker(error=True)
    run_worker(store, run_id, worker)
    setattr(worker, field, value)
    with pytest.raises(ValueError, match="frozen"):
        run_worker(store, run_id, worker)
    assert worker.calls == 1


def test_contract_change_requires_new_run(vault, monkeypatch):
    store, queue, run_id = review(vault)
    import evidencekg.review_queue as module

    monkeypatch.setattr(module, "PROMPT_VERSION", "changed")
    with pytest.raises(ValueError, match="new run"):
        queue.next_review_task(run_id, "synthetic")
    assert not store.rows("SELECT * FROM task_attempts")


def test_input_budget_retains_whole_source_and_calls_no_worker(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    original = "😀" * 1500 + " LAST QUALIFIER"
    (root / "huge.txt").write_text(original)
    store = initialize(tmp_path / "state", root, {"max_input_bytes": 32768, "segment_chars": 10000})
    try:
        queue = Queue(store)
        status = queue.start_review(ingest(store), "Q" * 19900)
        worker = SyntheticWorker()
        assert run_worker(store, status["run_id"], worker)["state"] == "failed_unresolved"
        assert worker.calls == 0 and status["unresolved_input_failures"] > 0
        packet = json.loads(
            store.get(store.one("SELECT * FROM review_tasks WHERE kind='source'")["input_manifest_sha"])
        )
        assert packet["segments"][0]["text"] == original
        assert status["source_scheduled"] == 1 and status["source_delivered"] == 0
    finally:
        store.close()


def test_discovery_has_joint_originals_and_every_source(vault):
    root, store = vault
    (root / "origin.txt").write_text("Case: 123 Original deadline ONLY")
    (root / "lexical.txt").write_text("Amendment deadline changes")
    (root / "graph.txt").write_text("Case: 123 Contradictory condition")
    (root / "unrelated.txt").write_text("Azure heron departed")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review", max_rounds=1)["run_id"]
    primary = {s["id"] for s in queue._segments(queue.run(run_id)["snapshot_id"])}
    issued = False

    def edit(task, result):
        nonlocal issued
        if task["payload"]["kind"] == "source" and "Original" in task["payload"]["segments"][0]["text"]:
            result["unresolved_questions"] = ["What amendment changes the deadline?"]
            issued = True
        elif task["payload"]["kind"] == "reconsideration":
            result["unresolved_questions"] = ["New unresolved issue from reconsideration"]

    drain(queue, run_id, edit)
    assert issued
    packets = [
        json.loads(store.get(t["input_manifest_sha"]))
        for t in store.rows("SELECT * FROM review_tasks WHERE pass_id='C1'")
    ]
    reconsidered = {p["segments"][0]["id"] for p in packets if p["kind"] == "reconsideration"}
    assert reconsidered == primary
    joint = [p for p in packets if p["kind"] == "candidate_connection"]
    assert any("Amendment" in dump(p["segments"]) for p in joint)
    assert any("Contradictory" in dump(p["segments"]) for p in joint)
    assert all("Original deadline ONLY" in dump(p["segments"]) for p in joint)
    assert not any("Azure" in dump(p["segments"]) for p in joint)
    assert all(len(p["segments"]) == 2 for p in joint)
    discovery = json.loads(store.get(joint[0]["descriptor"]["discovery"]["artifact_sha"]))
    assert discovery["algorithm"] == "issue-token-and-origin-graph-v1"
    assert discovery["source_scope"] == "every primary snapshot segment independently reconsidered"
    status = queue.review_status(run_id)
    assert status["round_limit_reached"] and status["state"] == "scheduled_work_complete_unresolved"
    assert status["source_scheduled"] == status["source_delivered"] == status["source_validated"] == 4
    assert (
        status["graph_memberships"]
        == status["graph_memberships_scheduled"]
        == status["graph_memberships_returned"]
    )


def test_discovery_preview_continues_without_dropping_candidates(vault):
    root, store = vault
    for i in range(12):
        (root / f"{i}.txt").write_text(f"Deadline item {i}")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review", max_rounds=1)["run_id"]
    task = queue.next_review_task(run_id, "synthetic")
    result = empty_result(task)
    result["unresolved_questions"] = ["Deadline qualifier?"]
    submit(queue, task, result)
    drain(queue, run_id)
    packets = [
        json.loads(store.get(t["input_manifest_sha"]))
        for t in store.rows("SELECT * FROM review_tasks WHERE kind='candidate_connection'")
    ]
    assert len(packets) == 11
    preview = packets[0]["descriptor"]["discovery"]
    assert len(preview["items"]) == 8 and preview["next_offset"] == 8
    artifact = json.loads(store.get(preview["artifact_sha"]))
    assert len(artifact["candidates"]) == 11
    assert {p["descriptor"]["discovery"]["offset"] for p in packets} == set(range(11))


def test_scheduling_exception_does_not_erase_accepted_result(vault, monkeypatch):
    store, queue, run_id = review(vault)
    task = queue.next_review_task(run_id, "synthetic")
    result = empty_result(task)
    result["unresolved_questions"] = ["Deadline?"]
    submit(queue, task, result)

    def broken(*args):
        raise ValueError("synthetic discovery failure")

    monkeypatch.setattr(queue, "_discovery", broken)
    drain(queue, run_id)
    assert queue.review_status(run_id)["state"] == "failed_unresolved"
    assert queue.review_status(run_id)["scheduling_failures"] == 1
    assert all(
        a["output_sha"] and a["validation_status"] == "validated"
        for a in store.rows("SELECT * FROM task_attempts")
    )


def test_joint_interpretations_require_every_supplied_premise():
    segments = [{"id": f"s{i}", "extraction_id": f"e{i}", "text": "Original"} for i in range(3)]
    refs = [
        dict(segment_id=s["id"], extraction_id=s["extraction_id"], start=0, end=8, quote="Original")
        for s in segments
    ]
    result = empty_result({"task_id": "task", "input_sha": "hash"})
    result["interpretations"] = [
        dict(relationship="POSSIBLE", subject="", object="", explanation="Premises", evidence_refs=refs[:2])
    ]
    task = {"id": "task", "input_manifest_sha": "hash"}
    payload = {"segments": segments, "kind": "candidate_connection"}
    with pytest.raises(ValueError, match="premises"):
        validate_result(result, task, payload)
    result["interpretations"][0]["evidence_refs"] = refs
    validate_result(result, task, payload)
    refs[2]["quote"] = "tampered"
    with pytest.raises(ValueError, match="quotation"):
        validate_result(result, task, payload)


def test_historic_link_packets_deliver_both_versions(vault):
    root, store = vault
    (root / "x.txt").write_text("Original deadline 17:00")
    ingest(store)
    (root / "x.txt").write_text("Changed deadline 18:00")
    snapshot = ingest(store)
    queue = Queue(store)
    run_id = queue.start_review(snapshot, "Review")["run_id"]
    packets = [
        json.loads(store.get(t["input_manifest_sha"]))
        for t in store.rows("SELECT * FROM review_tasks WHERE pass_id='B'")
    ]
    history = [
        p
        for p in packets
        if p["descriptor"].get("explicit_link", {}).get("relation_type") == "OBSERVED_VERSION_AFTER"
    ]
    assert history and any("17:00" in dump(p["segments"]) and "18:00" in dump(p["segments"]) for p in history)
    status = queue.review_status(run_id)
    assert status["explicit_links_scheduled"] == status["explicit_links"] > 0
    assert status["explicit_links_returned"] == 0
    drain(queue, run_id)
    assert queue.review_status(run_id)["explicit_links_returned"] == status["explicit_links"]


@pytest.mark.parametrize("output", ["not JSON", '{"task_id":"a","task_id":"b"}', '{"value":NaN}'])
def test_cli_malformed_raw_output_retained(vault, output):
    store, queue, run_id = review(vault)
    worker = CLIWorker(
        [sys.executable, "-c", f"import sys; sys.stdin.read(); print({output!r})"], "synthetic-v1"
    )
    run_worker(store, run_id, worker, max_calls=1)
    attempt = store.one("SELECT * FROM task_attempts")
    assert attempt["validation_status"] == "worker_error"
    assert store.get(attempt["output_sha"]).decode().strip() == output


def test_cli_adapter_identity_and_credential_environment(vault, monkeypatch):
    store, queue, run_id = review(vault)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-secret")
    worker = CLIWorker(
        [
            sys.executable,
            "-c",
            "import os; print(os.getenv('OPENAI_API_KEY'), os.getenv('ANTHROPIC_API_KEY'))",
        ],
        "synthetic-v1",
    )
    with pytest.raises(WorkerOutputError) as error:
        worker.call({})
    assert error.value.raw_output.strip() == b"None None"
    run_worker(store, run_id, worker, max_calls=1)
    changed = CLIWorker([sys.executable, "-c", "print('{}')"], "synthetic-v1")
    with pytest.raises(ValueError, match="adapter identity"):
        run_worker(store, run_id, changed)


def test_unresolved_inventory_and_nonexhaustive_never_unqualified(vault):
    root, store = vault
    (root / "unknown.bin").write_bytes(b"\x00unsupported")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review")["run_id"]
    drain(queue, run_id)
    assert queue.review_status(run_id)["state"] == "scheduled_work_complete_unresolved"
    run_id = queue.start_review(store.snapshot()["id"], "Review", exhaustive=False)["run_id"]
    drain(queue, run_id)
    assert queue.review_status(run_id)["state"] == "scheduled_work_complete_unresolved"
