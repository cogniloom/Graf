import copy
import json

import pytest

from evidencekg.experiments import (
    Limits,
    _read,
    capture_arm,
    citation_blocks,
    complete_before_phase,
    evaluate,
    freeze_experiment,
    resolve_answer,
    summarize,
)


class FakeWorker:
    identity = {"adapter": "synthetic-only"}
    model = "fake"
    effort = "none"

    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    def call(self, stage, payload, schema):
        self.calls.append((stage, copy.deepcopy(payload), schema))
        if self.failure == "raise":
            exc = ValueError("synthetic error")
            exc.raw_output = b"malformed provider response"
            raise exc
        if self.failure == "interrupt":
            raise KeyboardInterrupt()
        if stage == "judge":
            return {"correct": True, "rationale": "Synthetic match", "uncertainty": "fake"}
        cid = payload["passages"][0]["blocks"][0]["citation_id"] if payload["passages"] else None
        sources = [{"citation_id": cid}] if cid else []
        if self.failure == "id":
            sources = [{"citation_id": "invented"}]
        if self.failure == "span":
            sources = [{"citation_id": cid, "start": 0, "end": 1}]
        return {"assertion": "Friday", "sources": sources, "uncertainty": "synthetic"}


@pytest.fixture
def setup(tmp_path):
    segments = {
        f"s{i}": dict(
            id=f"s{i}",
            extraction_id=f"e{i}",
            document_version_id=f"d{i}",
            text=f"Original passage {i}. The deadline is Friday.\n",
        )
        for i in range(15)
    }
    cases = [
        dict(
            id=f"../../case-{i}",
            question=f"When is deadline {i}?",
            split=split,
            expected_answer="GOLD_ONLY Friday",
            evidence_refs=[dict(segment_id="s0", extraction_id="e0", start=0, end=8, quote="Original")],
        )
        for i, split in enumerate(("dev", "test"))
    ]
    answer, judge = FakeWorker(), FakeWorker()
    args = dict(
        corpus_identity={"snapshot": "frozen", "document_count": 15},
        cases=cases,
        code_identity={"baseline": "original-sha"},
        answer_worker=answer,
        judge_worker=judge,
        source_loader=segments.__getitem__,
        expected_document_count=15,
    )
    return tmp_path / "experiment", args, segments, answer, judge


def freeze(setup, **changes):
    root, args, *_ = setup
    return freeze_experiment(root, **{**args, **changes})


def capture(exp, segments, arm="no_graph", identity="original"):
    calls = []

    def retriever(question, limit):
        calls.append((question, limit))
        assert "GOLD_ONLY" not in question
        return {"segments": list(segments.values()), "passes": 1}

    capture_arm(exp, arm, retriever, retrieval_identity={"code_sha": identity, "policy": "test"})
    return calls


def test_identical_protocol_before_gate_later_arm_and_resume(setup):
    exp = freeze(setup)
    _, _, segments, answer, judge = setup
    for arm in ("no_graph", "old_graph"):
        assert len(capture(exp, segments, arm)) == 2
    with pytest.raises(ValueError, match="before-phase gate"):
        capture(exp, segments, "optimized_graph", "new")
    with pytest.raises(ValueError, match="answer missing"):
        complete_before_phase(exp)
    report = evaluate(exp)
    assert len(answer.calls) == len(judge.calls) == 4
    for stage, payload, schema in answer.calls:
        assert stage == "answer"
        assert "GOLD_ONLY" not in json.dumps(payload)
        assert set(payload) == {"question", "passages"}
        assert len(payload["passages"]) == 12
        assert "start" not in json.dumps(schema)
        for passage in payload["passages"]:
            assert "".join(b["text"] for b in passage["blocks"]) == segments[passage["segment_id"]]["text"]
    assert answer.calls[0] == answer.calls[2]
    for _, payload, _ in judge.calls:
        assert "GOLD_ONLY" in payload["expected_answer"]
        assert set(payload) == {"question", "expected_answer", "candidate", "passages"}
    snapshots = {p: p.read_bytes() for p in exp.output.rglob("*.json")}
    gate = complete_before_phase(exp)
    assert gate["artifact_hashes"]
    assert evaluate(exp) == report
    assert len(answer.calls) == 4
    capture(exp, segments, "optimized_graph", "new")
    evaluate(exp, arms=["optimized_graph"])
    assert len(answer.calls) == 6
    assert all(p.read_bytes() == value for p, value in snapshots.items())
    assert not capture(exp, segments)
    counts = summarize(exp)["arms"]["no_graph"]["all"]
    assert counts["denominator"] == counts["correct"] == 2
    assert counts["segment_recall_at6"] == counts["document_recall_at12"] == 1
    assert counts["selected_text_bytes"] > 0


@pytest.mark.parametrize("drift", ["cases", "corpus", "worker", "arm", "artifact", "source"])
def test_frozen_drift(setup, drift):
    exp = freeze(setup)
    root, args, segments, answer, _ = setup
    capture(exp, segments)
    with pytest.raises(ValueError):
        if drift == "cases":
            changed = copy.deepcopy(args["cases"])
            changed[0]["question"] = "Changed question"
            freeze(setup, cases=changed)
        elif drift == "corpus":
            freeze(setup, corpus_identity={"snapshot": "changed", "document_count": 15})
        elif drift == "worker":
            answer.model = "changed"
            evaluate(exp)
        elif drift == "arm":
            capture(exp, segments, identity="changed")
        elif drift == "source":
            segments["s0"]["text"] += "changed"
            freeze(setup)
        else:
            path = next(root.glob("arm-*/case-*/packet.json"))
            value = json.loads(path.read_text())
            value["value"]["selected_segments"][0]["text"] = "tamper"
            path.write_text(json.dumps(value))
            evaluate(exp)


@pytest.mark.parametrize("failure", ["raise", "id", "span"])
def test_failed_denominator_and_raw_receipts_no_retry(setup, failure):
    exp = freeze(setup)
    root, _, segments, answer, judge = setup
    answer.failure = failure
    capture(exp, segments)
    report = evaluate(exp)
    counts = report["arms"]["no_graph"]["all"]
    assert counts["denominator"] == counts["failed_or_missing"] == counts["unscored"] == 2
    assert counts["correct"] == 0
    assert counts["segment_recall"] == 1
    assert not judge.calls
    for path in root.glob("arm-*/case-*/answer.json"):
        receipt = _read(path)
        assert receipt["status"] == "failed" and receipt["raw_output"]
        assert receipt["source_validation"] == {"valid": False}
    assert evaluate(exp) == report
    assert len(answer.calls) == 2


def test_judge_failure_retained_and_no_retry(setup):
    exp = freeze(setup)
    _, _, segments, answer, judge = setup
    judge.failure = "raise"
    capture(exp, segments)
    result = evaluate(exp)["arms"]["no_graph"]["all"]
    assert result["answer_completed"] == result["unscored"] == 2
    assert result["judge_completed"] == result["correct"] == 0
    evaluate(exp)
    assert len(answer.calls) == len(judge.calls) == 2


def test_interrupted_intent_is_not_retried(setup):
    exp = freeze(setup)
    _, _, segments, answer, judge = setup
    capture(exp, segments)
    answer.failure = "interrupt"
    with pytest.raises(KeyboardInterrupt):
        evaluate(exp, split="dev")
    answer.failure = None
    counts = evaluate(exp, split="dev")["arms"]["no_graph"]["all"]
    assert len(answer.calls) == 1 and not judge.calls
    assert counts["unscored"] == 2


def test_citation_gap_free_unicode_and_invalid_span():
    segment = dict(id="s", extraction_id="e", document_version_id="d", text="A😀\nB é\nend")
    blocks, catalog = citation_blocks([segment], 3)
    assert "".join(b["text"] for b in blocks[0]["blocks"]) == segment["text"]
    raw = {"assertion": "A", "sources": [{"citation_id": next(iter(catalog))}], "uncertainty": ""}
    resolved = resolve_answer(raw, catalog, [segment])
    assert resolved["sources"][0]["quote"] == "A😀\n"
    catalog[next(iter(catalog))]["end"] = 100
    with pytest.raises(ValueError, match="span"):
        resolve_answer(raw, catalog, [segment])


def test_input_budget_whole_segments_and_missing_rows(setup):
    exp = freeze(setup, limits=Limits(max_evidence_bytes=1100))
    root, _, segments, answer, _ = setup
    segments["s14"]["text"] = "x" * 70000
    capture(exp, segments)
    for path in root.glob("arm-*/case-*/packet.json"):
        packet = _read(path)
        assert packet["evidence_payload_bytes"] <= 1100
        assert packet["excluded"]
        assert all(s["text"] == segments[s["id"]]["text"] for s in packet["selected_segments"])
    report = summarize(exp)["arms"]["no_graph"]["all"]
    assert report["denominator"] == report["unscored"] == 2
    evaluate(exp)
    assert len(answer.calls) == 2


def test_impossible_judge_envelope_rejected_before_calls(setup):
    _, args, _, answer, judge = setup
    args["cases"][0]["expected_answer"] = "z" * 60000
    with pytest.raises(ValueError, match="Judge envelope reservation"):
        freeze(setup)
    assert not answer.calls and not judge.calls
    with pytest.raises(ValueError, match="Judge envelope reservation"):
        freeze(setup, limits=Limits(max_input_bytes=4000, max_evidence_bytes=1000))


@pytest.mark.parametrize("extra", [0, 1])
def test_maximum_answer_boundary_and_judge_reservation_across_arms(setup, extra):
    from evidencekg.db import dump

    _, _, segments, answer, judge = setup
    segments["s1"]["text"] = "x" * 55200
    segments["s2"]["text"] = 'é"\\' * 12000
    limits = Limits()
    exp = freeze(setup, limits=limits)
    original = answer.call

    def maximum_answer(stage, payload, schema):
        raw = original(stage, payload, schema)
        raw["assertion"] = ""
        remaining = limits.max_output_bytes - len(dump(raw).encode())
        # Include UTF-8 and JSON escapes at the exact serialized-byte boundary.
        raw["assertion"] = 'é"\\' * (remaining // 6) + "x" * (remaining % 6 + extra)
        assert len(dump(raw).encode()) == limits.max_output_bytes + extra
        return raw

    answer.call = maximum_answer
    for arm in ("no_graph", "old_graph"):
        capture(exp, segments, arm)
    report = evaluate(exp)
    assert len(answer.calls) == 4
    assert len(judge.calls) == (0 if extra else 4)
    assert answer.calls[0] == answer.calls[2]
    for path in exp.output.glob("arm-*/case-*/packet.json"):
        packet = _read(path)
        assert packet["judge_reserved_input_bytes"] <= limits.max_input_bytes
        assert any(x["reason"] == "judge_envelope_byte_limit" for x in packet["excluded"])
    for arm in report["arms"].values():
        assert arm["all"]["judge_completed"] == (0 if extra else 2)
    if not extra:
        for path in exp.output.glob("arm-*/case-*/judge.json"):
            result = _read(path)
            packet = _read(path.with_name("packet.json"))
            assert result["input_bytes"] == packet["judge_reserved_input_bytes"]


def test_safe_paths_and_corpus_count(setup, tmp_path):
    _, args, segments, _, _ = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "link"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        freeze_experiment(linked / "new", **args)
    (outside / "unrelated").write_text("preserve")
    with pytest.raises(ValueError, match="new/empty"):
        freeze_experiment(outside, **args)
    with pytest.raises(ValueError, match="count"):
        freeze(setup, expected_document_count=1000)
    exp = freeze(setup, before_arms=("../../unsafe-arm",))
    capture(exp, segments, "../../unsafe-arm")
    assert not (tmp_path.parent / "unsafe-arm").exists()


def test_bad_retrieved_source_and_retrieval_failure_are_terminal(setup):
    exp = freeze(setup)
    _, _, segments, answer, _ = setup
    bad = copy.deepcopy(segments["s0"])
    bad["text"] = "fabricated"
    calls = []

    def retrieve(question, limit):
        calls.append(question)
        return {"segments": [bad]}

    capture_arm(exp, "no_graph", retrieve, retrieval_identity="v1")
    capture_arm(exp, "no_graph", retrieve, retrieval_identity="v1")
    counts = evaluate(exp)["arms"]["no_graph"]["all"]
    assert len(calls) == 2 and not answer.calls
    assert counts["denominator"] == counts["failed_or_missing"] == 2
    assert counts["segment_recall"] == 0


def test_exact_at6_vs_at12_and_declared_costs(setup):
    exp = freeze(setup)
    _, _, segments, _, _ = setup
    ranked = list(segments.values())[1:9] + [segments["s0"]]
    capture_arm(
        exp,
        "no_graph",
        lambda question, limit: {"segments": ranked, "costs": {"retrieval_passes": 2, "model_calls": 0}},
        retrieval_identity="v1",
    )
    counts = evaluate(exp)["arms"]["no_graph"]["all"]
    assert counts["segment_recall_at6"] == counts["document_recall_at6"] == 0
    assert counts["segment_recall_at12"] == counts["document_recall_at12"] == 1
    assert counts["retrieval_costs"] == {"retrieval_passes": 4, "model_calls": 0}
    assert counts["answer_model_calls"] == counts["judge_model_calls"] == 2
    assert counts["answer_input_bytes"] > 0


def test_gate_detects_missing_before_artifact(setup):
    exp = freeze(setup)
    _, _, segments, _, _ = setup
    for arm in ("no_graph", "old_graph"):
        capture(exp, segments, arm)
    evaluate(exp)
    receipt = complete_before_phase(exp)
    path = exp.output / next(iter(receipt["artifact_hashes"]))
    path.unlink()
    with pytest.raises(ValueError, match="artifact drift"):
        capture(exp, segments, "optimized_graph", "new")


def test_subscription_adapter_protocol_and_raw_failure(monkeypatch, tmp_path):
    from evidencekg.worker_adapters import experiment as module

    class Transport:
        def __init__(self, *args):
            self.worker = self
            self.identity = {"fake": True}
            self.schemas = {}
            self.trusted_instructions = {}
            self.state = tmp_path
            self.calls = []

        def call(self, stage, payload):
            self.calls.append((stage, payload))
            folder = self.state / "worker-calls" / "fake"
            folder.mkdir(parents=True)
            (folder / "response.json").write_text("malformed raw")
            other = self.state / "worker-calls" / "concurrent-judge"
            other.mkdir()
            (other / "response.json").write_text("OTHER CASE PRIVATE REFERENCE")
            exc = ValueError("Rejected provider output")
            exc.call_directory = folder
            raise exc

    monkeypatch.setattr(module, "ExistingLawcaseCodex", Transport)
    adapter = module.SubscriptionExperimentWorker(tmp_path, tmp_path, role="answer")
    with pytest.raises(ValueError, match="Separate"):
        adapter.call("judge", {}, {})
    with pytest.raises(ValueError) as error:
        adapter.call("answer", {"passages": []}, {"type": "array", "uniqueItems": True})
    assert error.value.raw_output["subscription_receipts"] == [
        {"directory": str(tmp_path / "worker-calls" / "fake"), "response": "malformed raw"}
    ]
    assert adapter.adapter.schemas["frozen_experiment_answer"] == {"type": "array"}
    assert "citation IDs" in adapter.adapter.trusted_instructions["frozen_experiment_answer"]


def test_deterministic_partitions_and_max_cases(setup):
    exp = freeze(setup)
    _, _, segments, answer, judge = setup
    capture(exp, segments)
    with pytest.raises(ValueError, match="partition"):
        evaluate(exp, partition_index=2, partition_count=2)
    with pytest.raises(ValueError, match="max_cases"):
        evaluate(exp, max_cases=0)
    for partition in range(3):
        evaluate(exp, partition_index=partition, partition_count=3, max_cases=1)
    # Repeated bounded calls pick up pending work, not already terminal cases.
    evaluate(exp, max_cases=1)
    assert len(answer.calls) == len(judge.calls) == 2
    assert summarize(exp)["arms"]["no_graph"]["all"]["correct"] == 2
    evaluate(exp, max_cases=1)
    assert len(answer.calls) == 2


def test_accidental_overlap_never_duplicates_calls(setup):
    import concurrent.futures
    import threading

    from evidencekg.experiments import FrozenExperiment

    exp = freeze(setup)
    _, _, segments, answer, judge = setup
    capture(exp, segments)
    entered, release = threading.Event(), threading.Event()
    original = answer.call

    def slow_call(stage, payload, schema):
        entered.set()
        assert release.wait(5)
        return original(stage, payload, schema)

    answer.call = slow_call
    other_answer, other_judge = FakeWorker(), FakeWorker()
    other = FrozenExperiment(exp.output, exp.config, other_answer, other_judge, exp.source_loader)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(evaluate, exp, split="dev")
        assert entered.wait(5)
        second = pool.submit(evaluate, other, split="dev")
        second.result(timeout=5)
        release.set()
        first.result(timeout=5)
    assert len(answer.calls) == len(judge.calls) == 1
    assert not other_answer.calls and not other_judge.calls
    evaluate(other, split="dev")
    assert not other_answer.calls


@pytest.mark.parametrize("stage", ["answer", "judge"])
@pytest.mark.parametrize("entered_worker", [False, True])
def test_unknown_call_costs_before_and_after_invocation(setup, monkeypatch, stage, entered_worker):
    from evidencekg import experiments as module

    exp = freeze(setup)
    _, _, segments, answer, judge = setup
    capture(exp, segments)
    worker = answer if stage == "answer" else judge
    if entered_worker:
        worker.failure = "interrupt"
    else:
        original = module._budget

        def interrupt_before_call(payload, schema, instruction, limits):
            if instruction == {"answer": module.ANSWER_INSTRUCTION, "judge": module.JUDGE_INSTRUCTION}[stage]:
                raise KeyboardInterrupt()
            return original(payload, schema, instruction, limits)

        monkeypatch.setattr(module, "_budget", interrupt_before_call)
    with pytest.raises(KeyboardInterrupt):
        evaluate(exp, split="dev")
    result = summarize(exp)["arms"]["no_graph"]
    assert len(worker.calls) == int(entered_worker)
    assert result["dev"][stage + "_model_calls"] == 0
    assert result["dev"][stage + "_unknown_attempts"] == 1
    assert result["dev"]["model_call_counts_are_lower_bounds"] is True
    assert result["test"][stage + "_unknown_attempts"] == 0
    assert result["test"]["model_call_counts_are_lower_bounds"] is False
    assert result["all"]["unscored"] == 2
    evaluate(exp, split="dev")
    assert len(worker.calls) == int(entered_worker)


@pytest.mark.parametrize("name", ["arm.json", "capture-start.json", "answer-start.json", "judge-start.json"])
def test_gate_binds_manifests_and_stage_intents(setup, name):
    from evidencekg.db import dump, sha

    exp = freeze(setup)
    _, _, segments, _, _ = setup
    for arm in ("no_graph", "old_graph"):
        capture(exp, segments, arm)
    evaluate(exp)
    gate = complete_before_phase(exp)
    path = next(exp.output.rglob(name))
    assert str(path.relative_to(exp.output)) in gate["artifact_hashes"]
    value = _read(path)
    value["tampered"] = True
    # Even a newly sealed artifact cannot replace provenance bound by the gate.
    path.write_text(dump({"sha256": sha(dump(value)), "value": value}))
    with pytest.raises(ValueError, match="Before-phase artifact drift"):
        evaluate(exp)


def test_adapter_without_bound_receipt_never_discovers_other_calls(monkeypatch, tmp_path):
    from evidencekg.worker_adapters import experiment as module

    class Transport:
        def __init__(self, *args):
            self.worker = self
            self.schemas = {}
            self.trusted_instructions = {}

        def call(self, stage, payload):
            folder = tmp_path / "worker-calls" / "other"
            folder.mkdir(parents=True)
            (folder / "response.json").write_text("OTHER CASE")
            raise ValueError("No bound receipt")

    monkeypatch.setattr(module, "ExistingLawcaseCodex", Transport)
    adapter = module.SubscriptionExperimentWorker(tmp_path, tmp_path, role="answer")
    with pytest.raises(ValueError) as error:
        adapter.call("answer", {}, {})
    assert error.value.raw_output == {"subscription_receipts": []}
