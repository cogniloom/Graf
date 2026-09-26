import json

import pytest

from evidencekg.benchmark import MAX_EVIDENCE_BYTES, retrieve, run_benchmark
from evidencekg.config import configure
from evidencekg.db import dump
from evidencekg.ingest import ingest
from evidencekg.retrieval import API


def fixture_case(vault):
    root, store = vault
    (root / "a.txt").write_text("Case: 123\n\nThe violet deadline is Friday.")
    (root / "b.txt").write_text("Case: 123\n\nPayment requires approval.")
    snapshot = ingest(store)
    segment = API(store).search(snapshot, "violet")["items"][0]
    ref = dict(
        segment_id=segment["id"],
        extraction_id=segment["extraction_id"],
        start=0,
        end=len(segment["text"]),
        quote=segment["text"],
    )
    return (
        store,
        snapshot,
        [
            {
                "id": "case/one",
                "question": "When is the violet deadline?",
                "expected_answer": "REFERENCE_ONLY_MARKER Friday",
                "evidence_refs": [ref],
                "category": "deadline",
            }
        ],
    )


class Worker:
    identity = {"adapter": "deterministic-test-v1"}
    model = "test-only"
    effort = "none"

    def __init__(self, fail=False, malformed=False):
        self.calls = []
        self.fail, self.malformed = fail, malformed

    def call(self, stage, payload, schema):
        self.calls.append((stage, payload, schema))
        if self.fail:
            raise RuntimeError("intentional failure")
        if stage == "judge":
            return {"correct": True, "rationale": "Matches fixture", "uncertainty": "test"}
        segment = payload["segments"][0]
        return {
            "assertion": "Friday",
            "sources": [
                {
                    "segment_id": segment["id"],
                    "extraction_id": segment["extraction_id"],
                    "start": 0,
                    "end": len(segment["text"]),
                    "quote": "invented" if self.malformed else segment["text"],
                }
            ],
            "uncertainty": "test",
        }


def test_paired_labels_blinding_control_and_resume(vault, tmp_path):
    store, snapshot, cases = fixture_case(vault)
    worker, judge = Worker(), Worker()
    output = tmp_path / "experiment"
    report = run_benchmark(store, cases, worker, output, snapshot_id=snapshot, judge_worker=judge)
    assert len(worker.calls) == len(judge.calls) == 3
    for stage, payload, _ in worker.calls:
        assert stage == "answer"
        assert "REFERENCE_ONLY_MARKER" not in dump(payload)
        assert "expected_answer" not in dump(payload)
    for _, payload, _ in judge.calls:
        assert "REFERENCE_ONLY_MARKER" in dump(payload)
        assert "arm" not in payload and "reasons" not in payload and "case_id" not in payload
    graph_payload = next(p for _, p, _ in worker.calls if p["reasons"])
    plain_payloads = [p for _, p, _ in worker.calls if not p["reasons"]]
    assert any(p["segments"] == graph_payload["segments"] for p in plain_payloads)
    assert all(v["denominator"] == 1 for v in report["summary"]["arms"].values())
    resumed = run_benchmark(store, cases, worker, output, snapshot_id=snapshot, judge_worker=judge)
    assert resumed == report
    assert len(worker.calls) == 3
    changed = [{**cases[0], "question": "Different question"}]
    before = (output / "config.json").read_bytes()
    with pytest.raises(ValueError, match="configuration differs"):
        run_benchmark(store, changed, worker, output, snapshot_id=snapshot, judge_worker=judge)
    assert (output / "config.json").read_bytes() == before


@pytest.mark.parametrize("failure", ["call", "citation"])
def test_failures_remain_in_denominator_and_are_not_retried(vault, tmp_path, failure):
    store, snapshot, cases = fixture_case(vault)
    worker = Worker(fail=failure == "call", malformed=failure == "citation")
    judge = Worker()
    output = tmp_path / "failed"
    report = run_benchmark(store, cases, worker, output, snapshot_id=snapshot, judge_worker=judge)
    for arm in report["summary"]["arms"].values():
        assert arm["denominator"] == arm["unscored"] == arm["missing_or_invalid"] == 1
        assert arm["correct"] == 0
        assert arm["citations_valid"] == 0
    assert not judge.calls
    artifacts = list(output.glob("*/*/answer.json"))
    assert len(artifacts) == 3
    for path in artifacts:
        attempt = json.loads(path.read_text())
        assert attempt["status"] == "failed"
        assert attempt["request_sha"] and attempt["prompt_sha"] and attempt["schema_sha"]
        if failure == "citation":
            assert attempt["output_sha"] and attempt["output"]["sources"][0]["quote"] == "invented"
    run_benchmark(store, cases, worker, output, snapshot_id=snapshot, judge_worker=judge)
    assert len(worker.calls) == 3


def test_retrieval_terms_counts_budgets_and_expansion(vault):
    store, snapshot, cases = fixture_case(vault)
    api = API(store)
    baseline = retrieve(api, snapshot, cases[0]["question"], "baseline")
    graph = retrieve(api, snapshot, cases[0]["question"], "graph")
    assert baseline["candidate_counts"]["per_term"] == {"deadline": 1, "violet": 1}
    assert baseline["candidate_counts"]["union"] == 1
    assert graph["candidate_counts"]["union"] >= 2
    assert baseline["segments"][0] == graph["segments"][0]
    for arm in (baseline, graph):
        assert arm["max_evidence_bytes"] == MAX_EVIDENCE_BYTES
        assert arm["selected_count"] <= 6
        assert arm["evidence_bytes"] <= MAX_EVIDENCE_BYTES
        assert all(API(store).segment(snapshot, s["id"]) == s for s in arm["segments"])
    limited = retrieve(api, snapshot, cases[0]["question"], "graph", limit=1)
    assert limited["candidate_counts"] == graph["candidate_counts"]
    assert limited["selected_count"] == 1
    assert retrieve(api, snapshot, cases[0]["question"], "graph") == graph


def test_source_diversity_and_full_pagination(vault):
    root, store = vault
    configure(store, {"segment_chars": 32})
    (root / "a.txt").write_text(("violet " + "x" * 24 + "\n") * 130)
    (root / "b.txt").write_text("violet")
    snapshot = ingest(store)
    result = retrieve(API(store), snapshot, "violet", "baseline", limit=2)
    assert result["candidate_counts"]["lexical"] == 131
    assert len({s["document_version_id"] for s in result["segments"]}) == 2


def test_invalid_reference_prevents_all_model_calls(vault, tmp_path):
    store, snapshot, cases = fixture_case(vault)
    cases[0]["evidence_refs"][0]["quote"] = "false quote"
    worker = Worker()
    with pytest.raises(ValueError, match="quotation"):
        run_benchmark(store, cases, worker, tmp_path / "bad", snapshot_id=snapshot)
    assert worker.calls == []


def test_interrupted_attempt_is_durable_and_not_reinvoked(vault, tmp_path):
    store, snapshot, cases = fixture_case(vault)

    class Interrupted(Worker):
        def call(self, *args):
            raise KeyboardInterrupt()

    output = tmp_path / "interrupt"
    with pytest.raises(KeyboardInterrupt):
        run_benchmark(store, cases, Interrupted(), output, snapshot_id=snapshot, same_evidence_control=False)
    worker = Worker()
    report = run_benchmark(store, cases, worker, output, snapshot_id=snapshot, same_evidence_control=False)
    assert len(worker.calls) == 1
    assert sum(r["answer_status"] == "interrupted" for r in report["rows"]) == 1
    assert all(a["denominator"] == 1 for a in report["summary"]["arms"].values())


def test_explicit_relation_beats_name_hub(vault):
    root, store = vault
    (root / "seed.txt").write_text("violet Case: 123 Alex Smith")
    (root / "explicit.txt").write_text("Case: 123")
    for i in range(12):
        (root / f"hub-{i}.txt").write_text("Alex Smith")
    snapshot = ingest(store)
    result = retrieve(API(store), snapshot, "violet", "graph", limit=2)
    assert result["segments"][0]["text"].startswith("violet")
    assert result["segments"][1]["text"] == "Case: 123"
    assert result["candidate_counts"]["union"] == 14


def test_attributed_observations_are_source_checked_and_frozen(vault, tmp_path, monkeypatch):
    from evidencekg import assurance
    from evidencekg.review_queue import Queue

    store, snapshot, cases = fixture_case(vault)
    run = Queue(store).start_review(snapshot, "review", enhanced=True)["run_id"]
    ref = cases[0]["evidence_refs"][0]
    record = {
        "type": "observation",
        "id": "obs",
        "task_id": "task",
        "worker_id": "fixture",
        "input_sha": "input",
        "result_sha": "result",
        "observation": {"semantic_query_terms": ["approval"], "sources": [ref]},
    }
    monkeypatch.setattr(
        assurance, "assurance_data", lambda *args, **kwargs: {"items": [record], "next_offset": None}
    )
    enhanced = retrieve(API(store), snapshot, "approval", "graph", enhanced_run_id=run)
    assert enhanced["candidate_counts"]["observations"] == 1
    reasons = enhanced["reasons"][ref["segment_id"]]
    assert any(
        r["type"] == "attributed_observation" and r["detail"]["attribution"]["worker_id"] == "fixture"
        for r in reasons
    )
    output = tmp_path / "enhanced"
    worker = Worker()
    run_benchmark(store, cases, worker, output, snapshot_id=snapshot, enhanced_run_id=run)
    record["result_sha"] = "different"
    with pytest.raises(ValueError, match="configuration differs"):
        run_benchmark(store, cases, worker, output, snapshot_id=snapshot, enhanced_run_id=run)
    record["observation"]["sources"] = [{**ref, "quote": "invented"}]
    with pytest.raises(ValueError, match="quotation"):
        retrieve(API(store), snapshot, "approval", "graph", enhanced_run_id=run)


def test_paraphrase_enters_full_lexical_budget(vault, monkeypatch):
    from evidencekg import assurance
    from evidencekg.review_queue import Queue

    root, store = vault
    for i in range(8):
        (root / f"literal-{i}.txt").write_text(f"approval administrative note {i}")
    (root / "paraphrase.txt").write_text("Permission was granted Friday.")
    snapshot = ingest(store)
    target = API(store).search(snapshot, "Permission")["items"][0]
    ref = {
        "segment_id": target["id"],
        "extraction_id": target["extraction_id"],
        "start": 0,
        "end": len(target["text"]),
        "quote": target["text"],
    }
    run = Queue(store).start_review(snapshot, "review", enhanced=True)["run_id"]
    record = {
        "type": "observation",
        "id": "obs",
        "task_id": "task",
        "worker_id": "fixture",
        "input_sha": "input",
        "result_sha": "result",
        "observation": {"semantic_query_terms": ["approval"], "sources": [ref]},
    }
    monkeypatch.setattr(
        assurance, "assurance_data", lambda *args, **kwargs: {"items": [record], "next_offset": None}
    )
    baseline = retrieve(API(store), snapshot, "Wann wurde approval?", "baseline", limit=6)
    graph = retrieve(API(store), snapshot, "Wann wurde approval?", "graph", limit=6, enhanced_run_id=run)
    assert baseline["candidate_counts"]["per_term"] == {"approval": 8}
    assert len(graph["segments"]) == len(baseline["segments"]) == 6
    assert target["id"] not in {s["id"] for s in baseline["segments"]}
    assert graph["segments"][2]["id"] == target["id"]
