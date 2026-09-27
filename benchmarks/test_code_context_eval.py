"""Deterministic offline tests; native calls are never made."""
import json
from collections import Counter

import pytest

from benchmarks import code_context_eval as ev
from benchmarks import native


def frozen(tmp_path):
    original, prior, contexts = [tmp_path / name for name in ("original", "prior", "contexts")]
    contexts.mkdir()
    cases = [dict(id=f"code-{i:02}", question=f"Question {i}", answerable=True,
                  expected_answer="frozen", required_evidence=[dict(path="x.py", quote="frozen")])
             for i in (1, 2)]
    for root in (original, prior):
        (root / "sources/code").mkdir(parents=True)
        (root / "sources/code/x.py").write_text("frozen source\n")
        native.write(root / "code-gold.json", cases)
        native.write(root / "run.json", dict(corpora={"code": native.inventory(root / "sources/code")},
                                           gold_hashes={"code": native.sha(root / "code-gold.json")}))
    for case in cases:
        native.write(original / f"retrieval-{case['id']}.json",
                     dict(question=case["question"], seconds=3.2, context={"old": case["id"]}, packet={}))
        native.write(contexts / f"{case['id']}.json", dict(seconds=0.1, context={"new": case["id"]}))
    output = tmp_path / "evaluation"
    ev.prepare(output, original, prior, contexts=contexts)
    return output, original, prior, contexts


def record(folder, status="answered", usage=True, judge=False, correct=True):
    folder.mkdir()
    (folder / "events.jsonl").write_text("raw event\n")
    answer = dict(correct=correct, complete=True, supported=True, rationale="evidence") if judge else dict(
        answer="frozen", abstain=False, citations=[dict(path="x.py", quote="frozen")])
    native.write(folder / "receipt.json", dict(status=status, error=None if status == "answered" else "failed",
                 seconds=2, answer=answer, usage=dict(input_tokens=10, cached_input_tokens=2, output_tokens=3)
                 if usage else None, root_usage=None, artifacts=native.inventory(folder)))


def test_schedule_is_paired_reproducible_and_randomized():
    ids = [f"code-{i}" for i in range(10)]
    rows = ev.paired_schedule(ids)
    assert rows == ev.paired_schedule(ids[::-1])
    assert rows != ev.paired_schedule(ids, seed=3)
    assert len(rows) == 40
    assert Counter((r["case_id"], r["arm"]) for r in rows) == Counter({(c, a): 2 for c in ids for a in ev.ARMS})
    assert len({r["trial"] for r in rows}) == len(rows)
    for left, right in zip(rows[::2], rows[1::2]):
        assert left["pair"] == right["pair"]
        assert {left["arm"], right["arm"]} == set(ev.ARMS)
    assert {r["arm"] for r in rows[::2]} == set(ev.ARMS)
    with pytest.raises(ValueError):
        ev.paired_schedule(ids, repetitions=0)


def test_prepare_preserves_inputs_prompts_and_refuses_reuse(tmp_path):
    output, original, prior, contexts = frozen(tmp_path)
    before = native.inventory(original)
    with pytest.raises(FileExistsError):
        ev.prepare(output, original, prior, contexts=contexts)
    assert native.inventory(original) == before
    assert (output / "inputs/code-gold.json").read_bytes() == (original / "code-gold.json").read_bytes()
    for arm, context in (("baseline", {"old": "code-01"}), ("treatment", {"new": "code-01"})):
        assert (output / f"inputs/prompt-code-01-{arm}.txt").read_text() == (
            ev.ANSWER_PREFIX + "Question 1" + ev.CONTEXT_PREFIX + json.dumps(context))
    assert ev.verify(output)["protocol_hash"] == ev.digest(ev.protocol())


def test_protocol_and_input_tampering_refused(tmp_path, monkeypatch):
    output, *_ = frozen(tmp_path)
    monkeypatch.setattr(ev, "JUDGE_PREFIX", "altered")
    with pytest.raises(ValueError, match="protocol changed"):
        ev.verify(output)
    monkeypatch.undo()
    (output / "inputs/treatment-code-01.json").write_text("{}")
    with pytest.raises(ValueError, match="input changed"):
        ev.verify(output)


def test_run_manifest_tampering_refused(tmp_path):
    output, *_ = frozen(tmp_path)
    path = output / "run.json"
    config = native.read(path)
    config["schedule"].reverse()
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="run changed"):
        ev.verify(output)


def test_all_failures_missing_results_and_usage_retained(tmp_path):
    output, *_ = frozen(tmp_path)
    rows = ev.verify(output)["schedule"]
    record(output / rows[0]["trial"])
    record(output / rows[0]["trial"] / "judge", status="failed", usage=False, judge=True)
    record(output / rows[1]["trial"], status="failed")
    (output / rows[2]["trial"]).mkdir()
    result = ev.report(output)
    assert len(result["trials"]) == 8
    assert [r["failures"][0]["stage"] for r in result["trials"][:3]] == ["judge", "answer", "answer"]
    assert result["trials"][0]["citation_gate"] is True
    assert result["trials"][0]["semantic_gate"] is None
    first = result["trials"][0]
    assert first["end_to_end_seconds_estimate"] == pytest.approx(
        2 + 3.2 + (0.1 if first["arm"] == "treatment" else 0))
    for summary in result["summaries"].values():
        assert summary["scheduled"] == 4
        assert summary["strict"]["rate"] is None
        assert summary["answers"]["usage"] is None
    judge_usage = result["summaries"][rows[0]["arm"]]["judges"]
    assert judge_usage["unknown_usage_calls"] == 1
    assert judge_usage["seconds"] == 2
    arm = result["summaries"][rows[1]["arm"]]
    assert arm["answers"]["known_usage_lower_bound"]["input_tokens"] == 10
    ev.report(output)
    assert len(list((output / "reports").glob("*.json"))) == 2


def test_complete_gate_denominators_and_exact_usage(tmp_path):
    output, *_ = frozen(tmp_path)
    rows = ev.verify(output)["schedule"]
    for i, row in enumerate(rows):
        record(output / row["trial"])
        record(output / row["trial"] / "judge", judge=True, correct=i != 0)
    result = ev.report(output)
    assert result["trials"][0]["semantic_gate"] is False
    assert result["trials"][0]["strict_gate"] is False
    for summary in result["summaries"].values():
        assert summary["answers"]["usage"]["input_tokens"] == 40
        assert summary["judges"]["usage"]["input_tokens"] == 40
        assert summary["citation"]["rate"] == 1
        assert summary["strict"]["evaluated"] == 4
    assert sum(s["strict"]["passes"] for s in result["summaries"].values()) == 7


@pytest.mark.parametrize("interrupted", [False, True])
def test_run_refuses_failed_or_interrupted_calls_before_auth(tmp_path, monkeypatch, interrupted):
    output, *_ = frozen(tmp_path)
    row = ev.verify(output)["schedule"][0]
    folder = output / row["trial"]
    if interrupted:
        folder.mkdir()
    else:
        record(folder, status="failed")
    monkeypatch.setattr(ev, "isolated_home", lambda *a: pytest.fail("must not authenticate"))
    with pytest.raises(ValueError, match="no automatic retry"):
        ev.run(output, tmp_path / "auth")


def test_anonymous_judge_and_distinct_gates():
    case = dict(question="why", expected_answer="yes", answerable=True,
                required_evidence=[dict(path="x", quote="yes")])
    answer = dict(answer="yes", abstain=False, citations=[dict(path="../secret", quote="yes")])
    payload, citation, abstention = ev.judge_payload(case, answer, {"x": "yes"})
    assert not citation and abstention
    assert set(payload) == {"question", "reference_answer", "answerable", "reference_evidence",
                            "candidate", "source_documents"}
    assert payload["source_documents"] == {"x": "yes"}


def test_changed_receipt_artifact_refused(tmp_path):
    output, *_ = frozen(tmp_path)
    row = ev.verify(output)["schedule"][0]
    folder = output / row["trial"]
    record(folder)
    (folder / "events.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="artifact changed"):
        ev.report(output)


def test_mocked_run_resume_uses_identical_native_protocol(tmp_path, monkeypatch):
    output, *_ = frozen(tmp_path)
    calls = []
    monkeypatch.setattr(ev, "isolated_home", lambda *a: tmp_path / "isolated")

    def call(folder, cwd, home, model, effort, prompt, schema, timeout):
        judging = schema == native.GRADE
        calls.append(dict(model=model, effort=effort, prompt=prompt, judging=judging))
        assert timeout == 1200
        assert cwd == output / "judge-cwd" if judging else cwd == output / "inputs/sources"
        record(folder, judge=judging)
        return native.read(folder / "receipt.json")

    monkeypatch.setattr(native, "call", call)
    ev.run(output, tmp_path / "auth")
    assert len(calls) == 16
    assert all((c["model"], c["effort"]) == ("gpt-6-luna", "low") for c in calls[:8])
    assert all((c["model"], c["effort"]) == ("gpt-6-astra", "medium") for c in calls[8:])
    assert all(c["prompt"].startswith(ev.ANSWER_PREFIX) for c in calls[:8])
    assert all(c["prompt"].startswith(ev.JUDGE_PREFIX) for c in calls[8:])
    assert not any('"arm"' in c["prompt"] or '"trial"' in c["prompt"] for c in calls[8:])
    ev.run(output, tmp_path / "auth")
    assert len(calls) == 16
    assert all(s["strict"]["rate"] == 1 for s in ev.report(output)["summaries"].values())


def test_rubric_issues_do_not_rewrite_gold(tmp_path):
    output, original, prior, contexts = frozen(tmp_path)
    for root in (original, prior):
        gold = native.read(root / "code-gold.json")
        gold[0]["required_evidence"][0]["quote"] = "absent authored gold quote"
        (root / "code-gold.json").write_text(json.dumps(gold))
        manifest = native.read(root / "run.json")
        manifest["gold_hashes"]["code"] = native.sha(root / "code-gold.json")
        (root / "run.json").write_text(json.dumps(manifest))
    revised = tmp_path / "rubric-eval"
    ev.prepare(revised, original, prior, contexts=contexts)
    result = ev.report(revised)
    assert len(result["rubric_issues"]) == 1
    assert result["rubric_issues"][0]["case_id"] == "code-01"
    assert native.sha(revised / "inputs/code-gold.json") == native.sha(original / "code-gold.json")


def test_reported_receipt_is_immutable(tmp_path):
    output, *_ = frozen(tmp_path)
    folder = output / ev.verify(output)["schedule"][0]["trial"]
    record(folder)
    ev.report(output)
    path = folder / "receipt.json"
    result = native.read(path)
    result["usage"]["input_tokens"] += 1
    path.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="receipt changed"):
        ev.report(output)
