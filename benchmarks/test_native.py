"""Regression checks for native matrix selection and incomplete-result reporting."""

import json

import pytest

from benchmarks.native import matrix, report, session_usage, sha, write


def test_exclusions_are_exact():
    catalog = {"models": [dict(slug=model, visibility=visibility,
                supported_reasoning_levels=[{"effort": e} for e in ("low", "high", "xhigh", "max", "ultra")])
                for model, visibility in (("gpt-6-astra", "list"), ("other", "list"), ("hidden", "hide"))]}
    rows = matrix(catalog)
    assert [r["effort"] for r in rows if r["model"] == "gpt-6-astra"] == ["low", "xhigh"]
    assert len([r for r in rows if r["model"] == "other"]) == 5
    assert not any(r["model"] == "hidden" for r in rows)


def fixture_run(tmp_path):
    write(tmp_path / "catalog.json", {"models": [dict(slug="example", visibility="list",
          supported_reasoning_levels=[{"effort": "low"}])]})
    write(tmp_path / "run.json", {"corpora": {"documents": {}}, "schedule": [
          dict(model="example", effort="low", kind="documents", case_id=str(i), trial=f"trial-{i}")
          for i in range(2)]})
    folder = tmp_path / "trial-0"
    folder.mkdir()
    (folder / "events.jsonl").write_text("raw evidence")
    write(folder / "receipt.json", dict(status="answered", seconds=3.5,
          usage=dict(input_tokens=100, cached_input_tokens=40, output_tokens=10),
          artifacts={"events.jsonl": sha(folder / "events.jsonl")}))
    write(folder / "score.json", {"passed": True})
    (folder / "judge").mkdir()
    write(folder / "judge" / "receipt.json", dict(seconds=1, usage=dict(
          input_tokens=20, cached_input_tokens=0, output_tokens=5)))
    return folder


def test_missing_trials_are_not_zero_usage_or_accuracy(tmp_path):
    fixture_run(tmp_path)
    report(tmp_path)
    result = json.loads((tmp_path / "summary.json").read_text())[0]
    assert result["scheduled"] == 2
    assert result["attempted"] == 1
    assert result["strict_passes"] == 1
    assert result["accuracy"] is None
    assert result["input_tokens"] is None
    assert result["cached_input_tokens"] is None


def test_modified_raw_evidence_rejected(tmp_path):
    folder = fixture_run(tmp_path)
    (folder / "events.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="artifact changed"):
        report(tmp_path)


def test_child_usage_is_included_without_counting_inherited_parent_records(tmp_path):
    def usage(value):
        return dict(input_tokens=value, cached_input_tokens=0, output_tokens=2)

    parent = dict(thread_id="root", session_id="root", response_id="parent-response", usage=usage(10))
    child = dict(thread_id="child", session_id="root", response_id="child-response", usage=usage(20))
    paths = []
    for thread_id, records in (("root", [parent]), ("child", [parent, child, child])):
        path = tmp_path / f"{thread_id}.jsonl"
        events = [dict(type="session_meta", payload=dict(id=thread_id, session_id="root",
                  parent_thread_id="root" if thread_id == "child" else None,
                  agent_path="/root/child" if thread_id == "child" else "/root"))]
        if thread_id == "root":
            events += [dict(type="response_item", payload=dict(type="function_call", name="spawn_agent", call_id="spawn")),
                       dict(type="response_item", payload=dict(type="function_call_output", call_id="spawn",
                                                               output='{"task_name":"/root/child"}'))]
        events += [dict(type="token_usage_record", payload=r) for r in records]
        events += [dict(type="event_msg", payload=dict(type="task_complete"))]
        path.write_text("\n".join(json.dumps(e) for e in events))
        paths.append(path)
    result = session_usage(paths, "root", usage(10))
    assert result["usage"] == dict(input_tokens=30, cached_input_tokens=0, output_tokens=4)
    assert len(result["responses"]) == 2
    with pytest.raises(ValueError, match="child session receipt"):
        session_usage(paths[:1], "root", usage(10))
    with pytest.raises(ValueError, match="disagree"):
        session_usage(paths, "root", usage(11))
    paths[1].write_text("\n".join(paths[1].read_text().splitlines()[:-1]))
    with pytest.raises(ValueError, match="unfinished child"):
        session_usage(paths, "root", usage(10))


def test_failed_judge_cost_is_retained_without_score(tmp_path):
    folder = fixture_run(tmp_path)
    (folder / "score.json").unlink()
    report(tmp_path)
    result = json.loads((tmp_path / "grading-usage.json").read_text())
    assert result["calls"] == 1
    assert result["seconds"] == 1
    assert result["usage"]["input_tokens"] == 20
    assert result["usage"]["output_tokens"] == 5


def test_usage_gap_halts_configuration_and_withholds_totals(tmp_path):
    from benchmarks.native import halted_configurations

    fixture_run(tmp_path)
    folder = tmp_path / "trial-1"
    folder.mkdir()
    write(folder / "receipt.json", dict(status="failed", seconds=5, usage=None,
          error="Native usage reconciliation failed: unfinished child", artifacts={}))
    config = json.loads((tmp_path / "run.json").read_text())
    assert halted_configurations(tmp_path, config) == {("example", "low"): "trial-1"}
    report(tmp_path)
    result = json.loads((tmp_path / "summary.json").read_text())[0]
    assert result["status"] == "halted_incomplete"
    assert result["input_tokens"] is None
    assert result["accuracy"] is None


def test_other_failure_still_blocks(tmp_path):
    from benchmarks.native import halted_configurations

    folder = fixture_run(tmp_path)
    receipt = json.loads((folder / "receipt.json").read_text())
    receipt.update(status="failed", error="authentication error")
    (folder / "receipt.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="no automatic retry"):
        halted_configurations(tmp_path, json.loads((tmp_path / "run.json").read_text()))
