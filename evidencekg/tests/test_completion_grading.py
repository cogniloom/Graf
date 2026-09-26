"""Synthetic contract tests only: fake decisions do not prove semantic accuracy."""

import copy
import json
from types import SimpleNamespace

import pytest

from evidencekg import completion_grading as cg
from evidencekg.db import dump, sha
from evidencekg.experiments import (
    _locked,
    _read,
    _write,
    capture_arm,
    complete_before_phase,
    evaluate,
    freeze_experiment,
)


class OriginalWorker:
    identity = "original-fake"
    model = "fake-answer-model"
    effort = "none"

    def __init__(self, assertion="The source does not establish legal acceptance."):
        self.assertion = assertion
        self.calls = []

    def call(self, stage, payload, schema):
        self.calls.append((stage, copy.deepcopy(payload), copy.deepcopy(schema)))
        if stage == "judge":
            return {"correct": True, "rationale": "Fake original accepts safe refusals", "uncertainty": ""}
        assert "GOLD_ONLY" not in dump(payload)
        return {
            "assertion": self.assertion,
            "uncertainty": "Only the supplied source is assessed.",
            "sources": [{"citation_id": payload["passages"][0]["blocks"][0]["citation_id"]}],
        }


class Grader:
    identity = "synthetic-independent-grader"
    model = "fake-grader-model"
    effort = "none"

    def __init__(self, behavior=None, outcome="answered_correctly"):
        self.calls = []
        self.behavior = behavior
        self.outcome = outcome

    def call(self, stage, payload, schema):
        assert stage == "completion"
        self.calls.append((stage, copy.deepcopy(payload), copy.deepcopy(schema)))
        if self.behavior:
            return self.behavior(payload, schema)
        return {
            "results": [
                {
                    "evaluation_id": i["evaluation_id"],
                    "outcome": self.outcome,
                    "rationale": "Synthetic decision, not semantic verification",
                    "uncertainty": "fake",
                }
                for i in payload["items"]
            ]
        }


def fixture(tmp_path, n=5, assertion=None, source_text=None):
    sources = {}
    for key, text in {
        "gold": "GOLD_ONLY: On 12 March the author records no legal acceptance, subject to signed approval.",
        "candidate": source_text
        or "Document dated 14 March: the 12 March event was not legal acceptance; approval is required.",
    }.items():
        sources[key] = dict(
            id=key, extraction_id="extract-" + key, document_version_id="document-" + key, text=text
        )
    cases = [
        dict(
            id=f"SECRET_CASE_{i}",
            split="dev" if i % 2 else "test",
            question=f"Does source {i} establish legal acceptance of the 12 March event?",
            expected_answer="GOLD_ONLY: No, approval is required; the document date is 14 March.",
            evidence_refs=[
                dict(
                    segment_id="gold",
                    extraction_id="extract-gold",
                    start=0,
                    end=len(sources["gold"]["text"]),
                    quote=sources["gold"]["text"],
                )
            ],
        )
        for i in range(n)
    ]
    answer = OriginalWorker() if assertion is None else OriginalWorker(assertion)
    judge = OriginalWorker()
    exp = freeze_experiment(
        tmp_path / "original",
        corpus_identity={"snapshot": "synthetic", "document_count": 2},
        cases=cases,
        code_identity="frozen-original",
        answer_worker=answer,
        judge_worker=judge,
        source_loader=sources.__getitem__,
        expected_document_count=2,
    )
    for arm in ("no_graph", "old_graph"):
        capture_arm(
            exp,
            arm,
            lambda question, limit: {"segments": [sources["candidate"]]},
            retrieval_identity={"rank": "SECRET_RANK", "model": "SECRET_MODEL"},
        )
    evaluate(exp)
    return SimpleNamespace(exp=exp, sources=sources, answer=answer, judge=judge, root=tmp_path)


def freeze(fx, worker=None, **options):
    worker = worker or Grader()
    return cg.freeze_completion_grading(
        fx.exp.output, fx.root / "strict", source_loader=fx.sources.__getitem__, worker=worker, **options
    )


def rows(fx, arm="no_graph"):
    return [
        fx.exp.output / ("arm-" + sha(arm)) / ("case-" + sha(case["id"])) for case in fx.exp.config["cases"]
    ]


def rewrite(path, value):
    # Deliberate corruption even with a matching envelope to exercise semantic binding.
    path.write_text(dump({"value": value, "sha256": sha(dump(value))}))


def test_blind_judging_preserves_originals_and_gold_never_reaches_answer_model(tmp_path):
    fx = fixture(tmp_path)
    original = {p: p.read_bytes() for p in fx.exp.output.rglob("*.json")}
    calls_before = len(fx.answer.calls)
    g = freeze(fx)
    result = cg.grade(g, ["no_graph", "old_graph"])
    assert len(g.worker.calls) == 4  # Five items per arm, maximum four each.
    assert len(fx.answer.calls) == calls_before
    assert all(p.read_bytes() == data for p, data in original.items())
    for _, payload, schema in g.worker.calls:
        wire = dump(payload)
        assert not any(
            secret in wire
            for secret in (
                "no_graph",
                "old_graph",
                "SECRET_CASE",
                "SECRET_RANK",
                "SECRET_MODEL",
                "fake-answer-model",
                "segment_id",
                "extraction_id",
                "document_version_id",
                "correct",
            )
        )
        for item in payload["items"]:
            assert set(item) == {
                "evaluation_id",
                "question",
                "expected_answer",
                "reference_quotes",
                "candidate",
            }
            assert item["reference_quotes"] == [fx.sources["gold"]["text"]]
            assert item["candidate"]["cited_quotes"] == [fx.sources["candidate"]["text"]]
            assert item["candidate"]["uncertainty"]
            assert len(item["evaluation_id"]) == 64
        assert schema == cg.grading_schema([i["evaluation_id"] for i in payload["items"]])
    counts = result["arms"]["no_graph"]
    assert counts["all"]["denominator"] == counts["all"]["successful_answers"] == 5
    assert counts["dev"]["denominator"] == 2
    assert counts["test"]["denominator"] == 3
    assert counts["all"]["known_calls"] == 2
    assert cg.assert_complete(g, ["no_graph", "old_graph"]) == result
    assert cg.grade(g, ["no_graph", "old_graph"]) == result
    assert len(g.worker.calls) == 4


def test_instruction_contract_safe_refusal_vs_completed_negative_and_incidental_details(tmp_path):
    # This asserts instructions and accounting, not an LLM's understanding.
    instruction = cg.INSTRUCTION
    for required in (
        "safe, justified",
        "NOT successful",
        "completed negative answer",
        "legal acceptance",
        "Incidental extra gold details",
        "conditions",
        "negations",
        "event versus document dates",
        "Gold evidence cannot supply missing support",
    ):
        assert required in instruction
    fx = fixture(tmp_path, n=1, assertion="I cannot answer because evidence is missing.")
    g = freeze(fx, Grader(outcome="abstained"))
    report = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert _read(rows(fx)[0] / "judge.json")["output"]["correct"] is True
    assert report["outcomes"]["abstained"] == 1
    assert report["successful_answers"] == 0
    assert report["successful_answer_fraction_all_cases"] == 0


@pytest.mark.parametrize("target", ["gold", "candidate"])
def test_exact_source_validation_precedes_calls_and_rejects_later_drift(tmp_path, target):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx)
    fx.sources[target]["text"] += " Drift outside quoted span also rejected."
    with pytest.raises(ValueError, match="source drift"):
        cg.grade(g, ["no_graph"])
    assert not g.worker.calls


@pytest.mark.parametrize("target", ["gold_ref", "candidate_ref", "resolved", "loader_identity"])
def test_bad_reference_and_source_binding_fail_closed(tmp_path, target):
    fx = fixture(tmp_path, n=1)
    if target == "gold_ref":
        config = _read(fx.exp.output / "experiment.json")
        config["cases"][0]["evidence_refs"][0]["quote"] = "invented"
        config["cases_sha"] = sha(dump(config["cases"]))
        rewrite(fx.exp.output / "experiment.json", config)
    elif target in {"candidate_ref", "resolved"}:
        path = rows(fx)[0] / "answer.json"
        answer = _read(path)
        if target == "candidate_ref":
            answer["raw_output"]["sources"][0]["citation_id"] = "invented"
        else:
            answer["output"]["sources"][0]["quote"] = "invented"
        rewrite(path, answer)
    else:
        fx.sources["gold"]["id"] = "different"
    g = freeze(fx)
    with pytest.raises((ValueError, cg.jsonschema.ValidationError)):
        cg.grade(g, ["no_graph"])
    assert not g.worker.calls


def test_batches_split_without_truncating_and_budget_counts_schema_instruction(tmp_path):
    fx = fixture(tmp_path, n=5)
    g = freeze(fx)
    original = g.verify()
    items = [cg._prepare_item(g, original, "no_graph", c)["payload"] for c in original["cases"]]
    bound = cg._size(items[:2])
    assert bound > len(dump({"items": items[:2]}).encode())
    limited = cg.freeze_completion_grading(
        fx.exp.output,
        fx.root / "limited",
        source_loader=fx.sources.__getitem__,
        worker=Grader(),
        max_input_bytes=bound,
    )
    report = cg.grade(limited, ["no_graph", "old_graph"])
    assert [len(p["items"]) for _, p, _ in limited.worker.calls] == [2, 2, 1, 2, 2, 1]
    assert all(cg._size(p["items"]) <= bound for _, p, _ in limited.worker.calls)
    assert all(
        i["candidate"]["cited_quotes"] == [fx.sources["candidate"]["text"]]
        for _, p, _ in limited.worker.calls
        for i in p["items"]
    )
    assert report["arms"]["old_graph"]["all"]["successful_answers"] == 5
    tiny = cg.freeze_completion_grading(
        fx.exp.output,
        fx.root / "tiny",
        source_loader=fx.sources.__getitem__,
        worker=Grader(),
        max_input_bytes=100,
    )
    counts = cg.grade(tiny, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["unscored"] == counts["ungradable"] == counts["denominator"] == 5
    assert counts["ungradable_reasons"] == {"input_budget_exceeded": 5}
    assert not tiny.worker.calls


@pytest.mark.parametrize("mode", ["missing", "duplicate", "foreign", "extra", "bad_outcome"])
def test_exact_output_identity_rejects_missing_duplicate_or_cross_job(tmp_path, mode):
    fx = fixture(tmp_path, n=2)

    def corrupt(payload, schema):
        results = Grader().call("completion", payload, schema)["results"]
        if mode == "missing":
            results.pop()
        elif mode == "duplicate":
            results[1] = results[0]
        elif mode == "foreign":
            results[0]["evaluation_id"] = "other-job-id"
        elif mode == "extra":
            results.append(copy.deepcopy(results[0]))
        else:
            results[0]["outcome"] = "safe_refusal_correct"
        return {"results": results}

    g = freeze(fx, Grader(corrupt))
    counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["failed"] == counts["unscored"] == 2
    assert counts["successful_answers"] == 0
    assert counts["known_calls"] == 1
    stored = _read(next(g.output.rglob("result.json")))
    assert stored["raw_output"]["results"] is not None
    assert stored["output_sha"] == sha(dump(stored["raw_output"]))
    cg.grade(g, ["no_graph"])
    assert len(g.worker.calls) == 1


def test_cross_job_ids_are_unique(tmp_path):
    fx = fixture(tmp_path, n=1)
    first = freeze(fx)
    cg.grade(first, ["no_graph"])
    stale_output = _read(next(first.output.rglob("result.json")))["raw_output"]
    other = cg.freeze_completion_grading(
        fx.exp.output,
        fx.root / "other",
        source_loader=fx.sources.__getitem__,
        worker=Grader(lambda *_: stale_output),
    )
    assert cg.grade(other, ["no_graph"])["arms"]["no_graph"]["all"]["failed"] == 1


@pytest.mark.parametrize("interrupt", [False, True])
def test_unknown_failure_and_interrupt_store_raw_block_gate_never_retry(tmp_path, interrupt):
    fx = fixture(tmp_path, n=1)

    def fail(*_):
        exc = KeyboardInterrupt("interrupted") if interrupt else TimeoutError("unknown provider outcome")
        exc.raw_output = b"unparsed partial output"
        exc.call_directory = "/synthetic/exact-call"
        raise exc

    g = freeze(fx, Grader(fail))
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            cg.grade(g, ["no_graph"])
    else:
        cg.grade(g, ["no_graph"])
    stored = _read(next(g.output.rglob("result.json")))
    assert stored["status"] == "unknown"
    assert stored["raw_output"]["base64"]
    assert stored["call_directory"] == "/synthetic/exact-call"
    counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["unknown"] == counts["unknown_calls"] == 1
    assert counts["known_calls"] == 0
    assert len(g.worker.calls) == 1
    with pytest.raises(ValueError, match="unknown"):
        cg.assert_complete(g, ["no_graph"])


def test_intent_without_result_is_unknown_and_overlapping_lock_makes_no_call(tmp_path):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx)
    plan = cg._plan(g, "no_graph")
    batch = plan["batches"][0]
    row = cg._arm_folder(g, "no_graph") / ("batch-" + batch["batch_id"])
    row.mkdir()
    with _locked(row):
        counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["pending"] == 1
    assert not g.worker.calls
    _write(row / "intent.json", cg._request(g, plan, batch))
    counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["unknown_calls"] == counts["unknown"] == 1
    assert not g.worker.calls
    with pytest.raises(ValueError, match="unknown"):
        cg.assert_complete(g, ["no_graph"])


def test_two_process_policy_partitions_independent_workers_and_overlap(tmp_path):
    fx = fixture(tmp_path, n=12)
    first = freeze(fx, partition_count=2)
    second = freeze(fx, partition_count=2)
    cg.grade(first, ["no_graph"], partition_index=0)
    with pytest.raises(ValueError, match="pending"):
        cg.assert_complete(first, ["no_graph"])
    cg.grade(second, ["no_graph"], partition_index=1)
    ids1 = {i["evaluation_id"] for _, p, _ in first.worker.calls for i in p["items"]}
    ids2 = {i["evaluation_id"] for _, p, _ in second.worker.calls for i in p["items"]}
    assert ids1 and ids2 and not ids1 & ids2
    assert len(ids1 | ids2) == 12
    count = len(first.worker.calls)
    cg.grade(first, ["no_graph"], partition_index=1)
    assert len(first.worker.calls) == count
    assert cg.assert_complete(first, ["no_graph"])["arms"]["no_graph"]["all"]["graded"] == 12


def test_later_optimized_arm_preserves_prior_grades_config_and_original_judges(tmp_path):
    fx = fixture(tmp_path, n=2)
    complete_before_phase(fx.exp)
    g = freeze(fx)
    cg.grade(g, ["no_graph", "old_graph"])
    before = {p: p.read_bytes() for p in g.output.rglob("*.json")}
    capture_arm(
        fx.exp,
        "optimized_graph",
        lambda question, limit: {"segments": [fx.sources["candidate"]]},
        retrieval_identity="new-later-arm",
    )
    evaluate(fx.exp, ["optimized_graph"])
    reopened = freeze(fx)
    assert reopened.config == g.config
    cg.grade(reopened, ["optimized_graph"])
    assert all(p.read_bytes() == data for p, data in before.items())
    assert cg.assert_complete(reopened, ["no_graph", "old_graph", "optimized_graph"])


def test_real_concurrent_processes_share_run_without_duplicate_calls(tmp_path):
    import multiprocessing

    fx = fixture(tmp_path, n=12)
    parent = freeze(fx, partition_count=2)
    # Freeze the plan before dispatch, as the documented integration does.
    cg.prepare(parent, ["no_graph"])
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    messages = context.Queue()

    def run_partition(index):
        worker = Grader()
        child = freeze(fx, worker, partition_count=2)
        assert ready.wait(10)
        cg.grade(child, ["no_graph"], partition_index=index)
        messages.put([i["evaluation_id"] for _, p, _ in worker.calls for i in p["items"]])

    processes = [context.Process(target=run_partition, args=(index,)) for index in (0, 1)]
    try:
        for process in processes:
            process.start()
        ready.set()
        completed = [messages.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
        assert not set(completed[0]) & set(completed[1])
        assert len(completed[0]) + len(completed[1]) == 12
        assert cg.assert_complete(parent, ["no_graph"])["arms"]["no_graph"]["all"]["graded"] == 12
        assert not parent.worker.calls
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        messages.close()


@pytest.mark.parametrize("state", ["missing", "unknown", "failed", "packet_failed"])
def test_missing_failed_original_answers_remain_denominator_and_gate_explicit(tmp_path, state):
    fx = fixture(tmp_path, n=2)
    row = rows(fx)[0]
    if state in {"missing", "unknown"}:
        (row / "answer.json").unlink()
        if state == "missing":
            (row / "answer-start.json").unlink()
    elif state == "packet_failed":
        rewrite(row / "packet.json", {"status": "failed", "error": "synthetic capture failure"})
    else:
        rewrite(row / "answer.json", {"status": "failed", "error": "synthetic original failure"})
    g = freeze(fx)
    counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["denominator"] == 2
    assert counts["successful_answers"] == counts["unscored"] == counts["ungradable"] == 1
    assert counts["successful_answer_fraction_all_cases"] == 0.5
    if state in {"missing", "unknown"}:
        with pytest.raises(ValueError, match="Original answering/judging missing or unknown"):
            cg.assert_complete(g, ["no_graph"])
    else:
        cg.assert_complete(g, ["no_graph"])


@pytest.mark.parametrize("artifact", ["experiment", "packet", "answer", "plan", "intent", "result"])
def test_corrupted_artifacts_fail_closed(tmp_path, artifact):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx)
    if artifact in {"plan", "intent", "result"}:
        cg.grade(g, ["no_graph"])
        path = next(g.output.rglob(artifact + ".json"))
    elif artifact == "experiment":
        path = fx.exp.output / "experiment.json"
    else:
        path = rows(fx)[0] / (artifact + ".json")
    envelope = json.loads(path.read_text())
    envelope["sha256"] = "corrupt"
    path.write_text(json.dumps(envelope))
    before = len(g.worker.calls)
    with pytest.raises(ValueError, match="Frozen artifact drift"):
        cg.grade(g, ["no_graph"])
    assert len(g.worker.calls) == before


def test_rechecks_source_and_input_hashes_on_resume(tmp_path):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx)
    cg.grade(g, ["no_graph"])
    fx.sources["candidate"]["text"] += " changed"
    with pytest.raises(ValueError, match="source drift"):
        cg.summarize(g, ["no_graph"])
    fx.sources["candidate"]["text"] = fx.sources["candidate"]["text"].removesuffix(" changed")
    result_path = next(g.output.rglob("result.json"))
    result = _read(result_path)
    result["request_sha"] = "wrong"
    rewrite(result_path, result)
    with pytest.raises(ValueError, match="hash mismatch"):
        cg.summarize(g, ["no_graph"])


def test_configuration_limits_and_explicit_arms(tmp_path):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx)
    for options in ({"max_batch_items": 5}, {"max_input_bytes": 90001}, {"partition_count": 3}):
        with pytest.raises(ValueError, match="limits"):
            freeze(fx, **options)
    with pytest.raises(ValueError, match="configuration drift"):
        freeze(fx, max_batch_items=1)
    with pytest.raises(ValueError, match="explicit sequence"):
        cg.grade(g, "no_graph")
    with pytest.raises(ValueError, match="separate"):
        cg.freeze_completion_grading(
            fx.exp.output, fx.exp.output / "strict", source_loader=lambda _: None, worker=Grader()
        )


def test_subscription_adapter_opt_in_role_and_exact_failure_receipt(tmp_path, monkeypatch):
    constructions = []
    actual = tmp_path / "exact-call"
    unrelated = tmp_path / "unrelated-call"
    actual.mkdir()
    unrelated.mkdir()
    (actual / "response.json").write_bytes(b"actual raw malformed response")
    (unrelated / "response.json").write_bytes(b"UNRELATED_SECRET")

    class Transport:
        identity = "synthetic-subscription"

        def __init__(self, *args):
            constructions.append(args)
            self.worker = self
            self.schemas = {}
            self.trusted_instructions = {}

        def call(self, stage, payload):
            assert self.trusted_instructions[stage] == cg.INSTRUCTION
            exc = ValueError("synthetic transport error")
            exc.call_directory = actual
            raise exc

    monkeypatch.setattr(cg, "ExistingLawcaseCodex", Transport)
    assert not constructions
    worker = cg.SubscriptionCompletionWorker(tmp_path, tmp_path / "worker-state")
    assert constructions == [(tmp_path, tmp_path / "worker-state", "gpt-6-astra", "medium", 600)]
    with pytest.raises(ValueError, match="only judges"):
        worker.call("answer", {}, {})
    payload = {"items": [{"evaluation_id": "opaque"}]}
    with pytest.raises(ValueError, match="transport error") as raised:
        worker.call("completion", payload, cg.grading_schema(["opaque"]))
    receipts = raised.value.raw_output["subscription_receipts"]
    assert [r["directory"] for r in receipts] == [str(actual)]
    import base64

    assert (
        base64.b64decode(receipts[0]["files"]["response.json"]["base64"]) == b"actual raw malformed response"
    )
    assert "UNRELATED_SECRET" not in dump(receipts)
    with pytest.raises(ValueError, match="requires gpt-6-astra"):
        cg.SubscriptionCompletionWorker(tmp_path, tmp_path, model="fallback")


def test_output_budget_retains_full_rejected_response(tmp_path):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx, max_output_bytes=10)
    counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["failed"] == 1
    raw = _read(next(g.output.rglob("result.json")))["raw_output"]
    assert len(dump(raw).encode()) > 10
    assert raw["results"][0]["rationale"] == "Synthetic decision, not semantic verification"


def test_concurrent_intent_and_result_publication_is_consistent(tmp_path, monkeypatch):
    fx = fixture(tmp_path, n=1)
    g = freeze(fx)
    cg.grade(g, ["no_graph"])
    result_path = next(g.output.rglob("result.json"))
    intent_path = result_path.with_name("intent.json")
    result_data, intent_data = result_path.read_bytes(), intent_path.read_bytes()
    result_path.unlink()
    intent_path.unlink()
    original_snapshot = cg._snapshot
    published = False

    def publish_between_reads(path):
        nonlocal published
        value = original_snapshot(path)
        if path in {intent_path, result_path} and not published:
            intent_path.write_bytes(intent_data)
            result_path.write_bytes(result_data)
            published = True
        return value

    monkeypatch.setattr(cg, "_snapshot", publish_between_reads)
    counts = cg.summarize(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["unknown"] == 1
    assert cg.summarize(g, ["no_graph"])["arms"]["no_graph"]["all"]["graded"] == 1


@pytest.mark.parametrize("terminal,known", [("turn.completed", True), ("turn.failed", False), ("malformed_event", False), ("malformed_receipt", False)])
def test_adapter_rejected_completed_response_is_failed_not_unknown(tmp_path, monkeypatch, terminal, known):
    folder = tmp_path / "exact-call"
    folder.mkdir()
    (folder / "response.json").write_text('{"wrong_schema":true}')
    (folder / "events.jsonl").write_text(dump({"type": terminal}) + "\n")
    (folder / "receipt.json").write_text(dump({"status": "failed", "finished_at": "now"}))
    if terminal == "malformed_event":
        (folder / "events.jsonl").write_text("[]\n")
    elif terminal == "malformed_receipt":
        (folder / "events.jsonl").write_text(dump({"type": "turn.completed"}) + "\n")
        (folder / "receipt.json").write_text("[]")

    class Transport:
        identity = "synthetic-subscription"

        def __init__(self, *args):
            self.worker = self
            self.schemas = {}
            self.trusted_instructions = {}

        def call(self, stage, payload):
            exc = ValueError("schema rejection after transport")
            exc.call_directory = folder
            raise exc

    monkeypatch.setattr(cg, "ExistingLawcaseCodex", Transport)
    fx = fixture(tmp_path / "experiment-fixture", n=1)
    worker = cg.SubscriptionCompletionWorker(tmp_path, tmp_path / "state")
    g = freeze(fx, worker=worker)
    counts = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert counts["failed"] == int(known)
    assert counts["unknown"] == int(not known)
    assert counts["known_calls"] == int(known)
    assert counts["unknown_calls"] == int(not known)
    result = _read(next(g.output.rglob("result.json")))
    assert result["raw_output"]["subscription_receipts"][0]["directory"] == str(folder)
    assert result["call_directory"] == str(folder)
    if known:
        cg.assert_complete(g, ["no_graph"])


def test_each_outcome_counted_once_and_swapped_plan_rejected_before_call(tmp_path):
    fx = fixture(tmp_path, n=5)

    def all_outcomes(payload, schema):
        result = Grader().call("completion", payload, schema)
        for row, item in zip(result["results"], payload["items"], strict=True):
            index = int(item["question"].split()[2])
            row["outcome"] = cg.OUTCOMES[index]
        return result

    g = freeze(fx, Grader(all_outcomes))
    report = cg.grade(g, ["no_graph"])["arms"]["no_graph"]["all"]
    assert report["outcomes"] == dict.fromkeys(cg.OUTCOMES, 1)
    assert report["successful_answers"] == 1
    cg.prepare(g, ["old_graph"])
    wrong_path = cg._arm_folder(g, "old_graph") / "plan.json"
    wrong_path.write_bytes((cg._arm_folder(g, "no_graph") / "plan.json").read_bytes())
    count = len(g.worker.calls)
    with pytest.raises(ValueError, match="Plan arm mismatch"):
        cg.grade(g, ["old_graph"])
    assert len(g.worker.calls) == count
