import json

import pytest
from test_core import empty_result

from evidencekg.assurance import assurance_data, assurance_status, discovery_preview
from evidencekg.db import dump
from evidencekg.ingest import ingest
from evidencekg.reports import task_plan_errors
from evidencekg.review_queue import Queue


def test_citation_catalog_partitions_verbatim_unicode_and_long_blocks():
    from evidencekg.assurance import citation_spans

    for text in ("", " Nur wenn bestätigt.\nNicht bezahlt!\n", "x" * 1201, "é🙂 " * 600):
        spans = citation_spans(text)
        assert "".join(span["quote"] for span in spans) == text
        previous = 0
        for span in spans:
            assert span["start"] == previous
            assert 0 < span["end"] - span["start"] <= 400
            assert text[span["start"] : span["end"]] == span["quote"]
            previous = span["end"]
        assert previous == len(text)
    with pytest.raises(ValueError):
        citation_spans("evidence", 0)


def ref(seg, start=0, end=None):
    end = len(seg["text"]) if end is None else end
    return dict(
        segment_id=seg["id"],
        extraction_id=seg["extraction_id"],
        start=start,
        end=end,
        quote=seg["text"][start:end],
    )


def result_for(task):
    result = empty_result(task)
    result.update(observations=[], wording_checks=[], assessments=[])
    payload = task["payload"]
    segments = {s["id"]: s for s in payload["segments"]}
    for marker in payload["descriptor"].get("risk_markers", []):
        result["wording_checks"].append(
            dict(
                marker_id=marker["id"],
                assessment="Qualifier considered",
                sources=[ref(segments[marker["segment_id"]], marker["start"], marker["end"])],
            )
        )
    for target in payload["descriptor"].get("targets", []):
        result["assessments"].append(
            dict(
                target_id=target["id"],
                verdict="supported",
                impact="low",
                explanation="Synthetic premise check",
                sources=[ref(s) for s in segments.values()],
            )
        )
    return result


def observation(seg):
    return dict(
        actors=[],
        action_event="Ending a contractual relationship",
        dates=[],
        obligations=[],
        conditions=[],
        negations=[],
        issue_keys=["Contract termination"],
        event_keys=["ending"],
        semantic_query_terms=["discharged"],
        categories=["employment"],
        sources=[ref(seg)],
        uncertainty="Synthetic attributed interpretation",
        epistemic_status="attributed_interpretation",
    )


def setup(vault, **kwargs):
    root, store = vault
    (root / "a.txt").write_text("The engagement ceased unless renewed.")
    (root / "b.txt").write_text("Sie wurde aus dem Dienst entlassen.")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review", enhanced=True, max_rounds=1, **kwargs)["run_id"]
    return store, queue, run_id


def submit(queue, task, result):
    return queue.submit_review_result(
        task["run_id"], task["task_id"], task["lease_id"], task["input_sha"], result
    )


def drain(queue, run_id, edit=None):
    count = 0
    while (task := queue.next_review_task(run_id, "synthetic")) is not None:
        result = result_for(task)
        if edit:
            edit(task, result)
        submit(queue, task, result)
        count += 1
        assert count < 300
    return count


def test_disconnected_wording_candidate_is_attributed_and_every_source_checked(vault):
    store, queue, run_id = setup(vault)
    graph_before = store.rows("SELECT * FROM explicit_links")

    def edit(task, result):
        if task["payload"]["kind"] in {"source", "critical_wording"}:
            result["observations"] = [observation(task["payload"]["segments"][0])]

    count = drain(queue, run_id, edit)
    assert count > 6
    assert store.rows("SELECT * FROM explicit_links") == graph_before
    tasks = [
        dict(t, payload=json.loads(store.get(t["input_manifest_sha"])))
        for t in store.rows("SELECT * FROM review_tasks")
    ]
    for task in tasks:
        for segment in task["payload"]["segments"]:
            assert "".join(span["quote"] for span in segment["citation_spans"]) == segment["text"]
    primary = {s["id"] for s in queue._segments(queue.run(run_id)["snapshot_id"])}
    for kind in ("source", "critical_wording", "source_reconciliation", "issue_reconsideration"):
        assert {t["payload"]["segments"][0]["id"] for t in tasks if t["kind"] == kind} == primary
    candidates = [t for t in tasks if t["kind"] == "semantic_candidate"]
    assert candidates and all({s["id"] for s in t["payload"]["segments"]} == primary for t in candidates)
    preview = discovery_preview(store, run_id)
    assert preview["observations"][0]["worker_id"] == "synthetic"
    assert all(len(g["member_segment_ids"]) == 2 for g in preview["groups"])
    status = assurance_status(store, run_id)
    assert status["phases"] == ["discovery", "reconciliation", "challenge"]
    assert not status["semantic_complete"]
    assert drain(queue, run_id, edit) == 0
    assert not task_plan_errors(store, run_id)
    pages, offset = [], 0
    while True:
        page = assurance_data(store, run_id, offset=offset, limit=2)
        pages.extend(page["items"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(pages) == page["total"]


def test_blind_payload_frozen_before_first_result_and_partial_source_preview(vault):
    store, queue, run_id = setup(vault)
    blind = store.rows("SELECT * FROM review_tasks WHERE kind='critical_wording'")
    before = [store.get(t["input_manifest_sha"]) for t in blind]
    while task := queue.next_review_task(run_id, "synthetic", kinds=["source"]):
        result = result_for(task)
        result["observations"] = [observation(task["payload"]["segments"][0])]
        result["uncertainty"] = "FIRST REVIEW SECRET"
        submit(queue, task, result)
    assert [store.get(t["input_manifest_sha"]) for t in blind] == before
    assert all(b"FIRST REVIEW SECRET" not in raw and b'"blind":true' in raw for raw in before)
    assert discovery_preview(store, run_id)["candidates"]
    assert not store.rows("SELECT * FROM review_tasks WHERE kind='semantic_candidate'")


def test_disagreement_and_unsupported_high_impact_are_human_flags(vault):
    store, queue, run_id = setup(vault)

    def edit(task, result):
        if task["payload"]["kind"] == "source":
            result["findings"] = [
                dict(
                    assertion="Termination is final",
                    epistemic_status="interpretation",
                    sources=[ref(task["payload"]["segments"][0])],
                    uncertainty="",
                )
            ]
        if task["payload"]["kind"] == "premise_challenge":
            result["assessments"][0].update(verdict="unsupported", impact="high")

    drain(queue, run_id, edit)
    status = queue.review_status(run_id)
    assert status["state"] == "scheduled_work_complete_unresolved"
    assert status["assurance"]["human_review_required"]
    flags = [r for r in assurance_data(store, run_id)["items"] if r["type"] == "human_review"]
    assert any(f["reason"] == "blind_review_difference_or_missing" for f in flags)
    assert any(f["reason"] == "unsupported:high" for f in flags)


def test_enhanced_task_deletion_fails_ledger(vault):
    store, queue, run_id = setup(vault)
    store.db.execute("DELETE FROM review_tasks WHERE kind='critical_wording'")
    assert task_plan_errors(store, run_id)
    drain(queue, run_id)
    assert queue.review_status(run_id)["state"] == "failed_unresolved"


@pytest.mark.parametrize("alter", ["quote", "date", "marker"])
def test_enhanced_citation_and_marker_rejection(vault, alter):
    store, queue, run_id = setup(vault)
    task = queue.next_review_task(run_id, "synthetic", kinds=["critical_wording"])
    # Select the English source with its mandatory condition marker.
    if not task["payload"]["descriptor"]["risk_markers"]:
        submit(queue, task, result_for(task))
        task = queue.next_review_task(run_id, "synthetic", kinds=["critical_wording"])
    result = result_for(task)
    obs = observation(task["payload"]["segments"][0])
    result["observations"] = [obs]
    if alter == "quote":
        obs["sources"][0]["quote"] = "fabricated"
    elif alter == "date":
        obs["dates"] = [
            dict(value="2030", role="event", sources=[dict(obs["sources"][0], segment_id="outside")])
        ]
    else:
        result["wording_checks"] = []
    with pytest.raises(ValueError):
        submit(queue, task, result)
    assert (
        store.one("SELECT * FROM task_attempts WHERE id=?", (task["lease_id"],))["validation_status"]
        == "invalid"
    )


def test_enhanced_invalid_results_exhaust_finite_resume(vault):
    store, queue, run_id = setup(vault)
    for _ in range(store.config()["max_attempts"]):
        task = queue.next_review_task(run_id, "synthetic")
        with pytest.raises(ValueError):
            submit(queue, task, empty_result(task))
    assert queue.next_review_task(run_id, "synthetic") is None
    assert queue.review_status(run_id)["state"] == "failed_unresolved"


def test_legacy_contract_does_not_gain_enhanced_fields(vault):
    root, store = vault
    (root / "x.txt").write_text("legacy")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review")["run_id"]
    task = queue.next_review_task(run_id, "synthetic")
    assert "assurance_version" not in task["payload"]
    assert "enhanced" not in json.loads(queue.run(run_id)["runner_config_json"])
    assert "observations" not in dump(empty_result(task))
    submit(queue, task, empty_result(task))


def test_candidate_groups_are_linear_and_preview_does_not_complete_partial_run(vault):
    root, store = vault
    for i in range(12):
        (root / f"source-{i}.txt").write_text(f"Passage number {i}")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review", enhanced=True)["run_id"]
    while task := queue.next_review_task(run_id, "synthetic", kinds=["source"]):
        result = result_for(task)
        obs = observation(task["payload"]["segments"][0])
        obs.update(issue_keys=[], event_keys=[], semantic_query_terms=[], categories=["one shared issue"])
        result["observations"] = [obs]
        submit(queue, task, result)
    preview = discovery_preview(store, run_id)
    assert len(preview["candidates"]) == 11
    assert len(preview["groups"][0]["member_segment_ids"]) == 12
    assert queue.review_status(run_id)["state"] == "running"
    assert queue.review_status(run_id)["assurance"]["stages"]["critical_wording"]["validated"] == 0


def test_assurance_read_helpers_accept_legacy_results(vault):
    root, store = vault
    (root / "source.txt").write_text("Original")
    queue = Queue(store)
    run_id = queue.start_review(ingest(store), "Review")["run_id"]
    task = queue.next_review_task(run_id, "synthetic")
    submit(queue, task, empty_result(task))
    assert assurance_status(store, run_id) == {"enhanced": False}
    assert discovery_preview(store, run_id)["observations"] == []
    assert isinstance(assurance_data(store, run_id)["items"], list)
