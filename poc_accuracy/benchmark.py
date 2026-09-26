"""Frozen original100 comparison; separate retrieval-only and context arms.

Only freeze/grading can see references. Local retrieval reads queries.json, which
contains question text and opaque identity only. Historical artifacts are read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from evidencekg.completion_grading import (
    SubscriptionCompletionWorker,
    freeze_completion_grading,
    grade,
    prepare,
    summarize,
)
from evidencekg.db import atomic, dump, sha
from evidencekg.experiments import (
    ANSWER_INSTRUCTION,
    FrozenExperiment,
    _attempt,
    _budget,
    _identity,
    _judge_reservation,
    _locked,
    _read,
    _write,
    answer_schema,
    capture_arm,
    freeze_experiment,
    resolve_answer,
)
from evidencekg.retrieval import API
from evidencekg.worker_adapters.experiment import SubscriptionExperimentWorker

from .sources import ReadOnlyStore

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".evidencekg-private/poc-accuracy-20260926"
OLD = PROJECT / ".evidencekg-private/benchmark-1000-20260925"
ARMS = ("poc_hybrid_legacy_context", "poc_hybrid_preserved_context")
BASELINES = ("no_graph", "old_graph", "optimized_mechanical_v2")


def hashes():
    files = list((PROJECT / "poc_accuracy").glob("*.py"))
    files += list((PROJECT / "evidencekg/src/evidencekg").rglob("*.py"))
    files += [PROJECT / "lawcase_worker.py", PROJECT / "corpus.py"]
    return {str(p.relative_to(PROJECT)): sha(p.read_bytes()) for p in sorted(files)}


def pin_cli(original):
    expected = original["answer_worker"]["identity"]["subscription"]["executable"]
    if not Path(expected).is_file():
        raise ValueError("Frozen subscription CLI unavailable")
    os.environ["PATH"] = str(Path(expected).parent) + os.pathsep + os.environ.get("PATH", "")


class GuardedAnswer(SubscriptionExperimentWorker):
    def call(self, *args, **kwargs):
        if (ROOT / "PAUSED.json").exists():
            raise ValueError("PoC paused; no new provider calls")
        return super().call(*args, **kwargs)


def open_environment():
    original = _read(OLD / "experiment/experiment.json")
    pin_cli(original)
    store = ReadOnlyStore(OLD / "model-vault")
    snapshot = original["corpus"]["snapshot_id"]
    if store.snapshot(snapshot)["manifest_sha"] != original["corpus"]["manifest_sha"]:
        raise ValueError("Original snapshot drift")
    api = API(store)
    answer = GuardedAnswer(PROJECT, PROJECT / ".lawcase", role="answer")
    judge = SubscriptionExperimentWorker(PROJECT, PROJECT / ".lawcase", role="judge")
    if _identity(answer) != original["answer_worker"] or _identity(judge) != original["judge_worker"]:
        raise ValueError("Frozen subscription identity mismatch")
    return original, store, api, snapshot, answer, judge


def check_protocol():
    protocol = _read(ROOT / "protocol.json")
    if hashes() != protocol["implementation"]:
        raise ValueError("PoC implementation drift after freeze")
    if sha((OLD / "experiment/experiment.json").read_bytes()) != protocol["original_config_file_sha"]:
        raise ValueError("Original experiment drift")
    return protocol


def freeze():
    original, store, api, snapshot, answer, judge = open_environment()
    try:
        tests = [c for c in original["cases"] if c["split"] == "test"]
        if len(tests) != 100 or len(original["cases"]) != 120 or original["corpus"]["document_count"] != 1000:
            raise ValueError("Expected frozen100 test,20 dev and1000 roots")
        historical = {}
        for arm in BASELINES:
            for c in original["cases"]:
                folder = OLD / "experiment" / ("arm-" + sha(arm)) / ("case-" + sha(c["id"]))
                for stage in ("packet", "answer"):
                    p = folder / (stage + ".json")
                    if _read(p)["status"] != "completed":
                        raise ValueError("Incomplete historical baseline")
                    historical[str(p.relative_to(OLD))] = sha(p.read_bytes())
        for p in (OLD / "completion-grading").rglob("*.json"):
            historical[str(p.relative_to(OLD))] = sha(p.read_bytes())
        protocol = {
            "version": "accuracy-poc-comparison-v1",
            "implementation": hashes(),
            "original_config_file_sha": sha((OLD / "experiment/experiment.json").read_bytes()),
            "corpus": original["corpus"],
            "test_ids": sorted(c["id"] for c in tests),
            "cases_sha": sha(dump(original["cases"])),
            "queries_sha": sha(
                dump([{"question_id": sha(c["id"]), "question": c["question"]} for c in tests])
            ),
            "baseline_hashes": historical,
            "arms": list(ARMS),
            "retrieval_inference": "Pinned local BGE-M3 and BGE reranker, CUDA,1024 tokens,batch4",
            "answer_policy": "Identical source passages/model/prompt/budgets; second arm adds verified original locator metadata; no source text rewritten",
            "grading_policy": "Original strict completion grader,120-case batching,20 cached no_graph dev answers for context only; denominator100",
            "limitations": [
                "Repeated benchmark, not fresh holdout",
                "Local ranker windows use max pooling, not joint reasoning across all windows",
                "Strict legacy grader receives cited text, not locator metadata; context benefit may be conservatively undercounted",
            ],
            "index_status": json.loads((ROOT / "index-status.json").read_text()),
            "ranker_identity": json.loads((ROOT / "ranker-identity.json").read_text()),
            "local_runtime": json.loads((ROOT / "environment.json").read_text()),
        }
        if (ROOT / "protocol.json").exists():
            if _read(ROOT / "protocol.json") != protocol:
                raise ValueError("Protocol freeze drift")
        else:
            _write(ROOT / "protocol.json", protocol)
        queries = [{"question_id": sha(c["id"]), "question": c["question"]} for c in tests]
        if (ROOT / "queries.json").exists():
            if _read(ROOT / "queries.json") != queries:
                raise ValueError("Question-only manifest drift")
        else:
            _write(ROOT / "queries.json", queries)
        exp = freeze_experiment(
            ROOT / "experiment",
            corpus_identity=original["corpus"],
            cases=original["cases"],
            code_identity={"protocol_sha": sha(dump(protocol))},
            answer_worker=answer,
            judge_worker=judge,
            source_loader=lambda sid: api.segment(snapshot, sid),
            before_arms=ARMS,
        )
        for k in ("limits", "prompts", "answer_worker", "judge_worker"):
            if exp.config[k] != original[k]:
                raise ValueError("Benchmark protocol mismatch:" + k)
        copied = {}
        for arm in ARMS:
            folder = exp.output / ("arm-" + sha(arm))
            folder.mkdir(exist_ok=True)
            descriptor = {
                "arm": arm,
                "retrieval_identity": {"protocol_sha": sha(dump(protocol)), "arm": arm},
                "experiment_sha": sha(dump(exp.config)),
            }
            if not (folder / "arm.json").exists():
                _write(folder / "arm.json", descriptor)
            elif _read(folder / "arm.json") != descriptor:
                raise ValueError("Arm descriptor drift")
            for case in original["cases"]:
                if case["split"] != "dev":
                    continue
                src = OLD / "experiment" / ("arm-" + sha("no_graph")) / ("case-" + sha(case["id"]))
                dst = folder / ("case-" + sha(case["id"]))
                dst.mkdir(exist_ok=True)
                for name in ("capture-start.json", "packet.json", "answer-start.json", "answer.json"):
                    raw = (src / name).read_bytes()
                    if (dst / name).exists():
                        if (dst / name).read_bytes() != raw:
                            raise ValueError("Cached development receipt drift")
                    else:
                        atomic(dst / name, raw)
                    copied[str((dst / name).relative_to(ROOT))] = {"source": str(src / name), "sha": sha(raw)}
        if not (ROOT / "development-provenance.json").exists():
            _write(ROOT / "development-provenance.json", copied)
        print(
            "Frozen original100 questions; same1000 roots; two separately attributed answer arms", flush=True
        )
    finally:
        store.close()


def experiment(env):
    original, store, api, snapshot, answer, judge = env
    cfg = _read(ROOT / "experiment/experiment.json")
    exp = FrozenExperiment(ROOT / "experiment", cfg, answer, judge, lambda sid: api.segment(snapshot, sid))
    exp.verify()
    return exp


def capture():
    protocol = check_protocol()
    env = open_environment()
    exp = experiment(env)
    try:
        # No reference data is passed into retrieval. All GPU packets were prepared
        # by the separate question-only process before answer capture.
        def retrieve(question, limit=12):
            key = sha(question)
            return _read(ROOT / "retrieval" / key / "packet.json")

        capture_arm(
            exp, ARMS[0], retrieve, retrieval_identity={"protocol_sha": sha(dump(protocol)), "arm": ARMS[0]}
        )
        # Derived arm has the exact same chosen source set; only locator delivery changes.
        folder = exp.output / ("arm-" + sha(ARMS[1]))
        with _locked(exp.output, "context-capture.lock"):
            for case in exp.config["cases"]:
                if case["split"] != "test":
                    continue
                row = folder / ("case-" + sha(case["id"]))
                if (row / "capture-start.json").exists():
                    continue
                _write(
                    row / "capture-start.json",
                    {"question_sha": sha(case["question"]), "derived_from": ARMS[0]},
                )
                control = _read(
                    exp.output / ("arm-" + sha(ARMS[0])) / ("case-" + sha(case["id"])) / "packet.json"
                )
                result = json.loads(dump(control))
                try:
                    if result["status"] != "completed":
                        raise ValueError("Control capture failed")
                    for p in result["payload"]["passages"]:
                        source = env[2].segment(env[3], p["segment_id"])
                        p["source_context"] = {
                            "locators": source["locators"],
                            "char_start": source["char_start"],
                            "char_end": source["char_end"],
                            "extraction_id": source["extraction_id"],
                        }
                    _budget(
                        result["payload"],
                        answer_schema(result["catalog"]),
                        ANSWER_INSTRUCTION,
                        exp.config["limits"],
                    )
                    result["judge_reserved_input_bytes"] = _judge_reservation(
                        case, result["payload"]["passages"], exp.config["limits"]
                    )
                    result["evidence_payload_bytes"] = len(dump(result["payload"]["passages"]).encode())
                    if result["evidence_payload_bytes"] > exp.config["limits"]["max_evidence_bytes"]:
                        raise ValueError("Context arm exceeds evidence budget; no truncation")
                    result["ablation"] = "same source selection; original locator metadata preserved"
                except Exception as exc:
                    result = {"status": "failed", "error": str(exc)}
                _write(row / "packet.json", result)
        print("Captured both arms", flush=True)
    finally:
        env[1].close()


def answer_partition(partition, count):
    check_protocol()
    env = open_environment()
    exp = experiment(env)
    try:
        for case in exp.config["cases"]:
            if case["split"] != "test" or int(sha(case["id"]), 16) % count != partition:
                continue
            for arm in ARMS:
                if (ROOT / "PAUSED.json").exists():
                    raise ValueError("PoC paused")
                row = exp.output / ("arm-" + sha(arm)) / ("case-" + sha(case["id"]))
                packet = _read(row / "packet.json")
                if packet["status"] != "completed":
                    raise ValueError("Failed capture:" + case["id"])
                result = _attempt(
                    row,
                    "answer",
                    exp.answer_worker,
                    packet["payload"],
                    answer_schema(packet["catalog"]),
                    exp.config["limits"],
                    lambda raw: resolve_answer(raw, packet["catalog"], packet["selected_segments"]),
                )
                print("ANSWER", partition, arm, case["id"], result["status"], flush=True)
                if result["status"] != "completed":
                    atomic(
                        ROOT / "PAUSED.json",
                        dump(
                            {"case_id": case["id"], "arm": arm, "stage": "answer", "status": result["status"]}
                        ).encode(),
                    )
                    raise ValueError("Provider attempt failed or unknown; inspect receipts before recovery")
    finally:
        env[1].close()


def grading_context():
    env = open_environment()
    prior = _read(OLD / "completion-grading/grading.json")
    job = freeze_completion_grading(
        ROOT / "experiment",
        ROOT / "grading",
        source_loader=lambda sid: env[2].segment(env[3], sid),
        worker=SubscriptionCompletionWorker(PROJECT, PROJECT / ".lawcase"),
        partition_count=prior["partition_count"],
        max_batch_items=prior["max_batch_items"],
        max_input_bytes=prior["max_input_bytes"],
        max_output_bytes=prior["max_output_bytes"],
    )
    for key in (
        "batching",
        "implementation_sha",
        "instruction",
        "max_batch_items",
        "max_input_bytes",
        "max_output_bytes",
        "partition_count",
        "schema_template",
        "version",
        "worker",
    ):
        if job.config[key] != prior[key]:
            raise ValueError("Strict grading policy drift:" + key)
    return env, job


def grading(phase, partition):
    check_protocol()
    if (ROOT / "PAUSED.json").exists():
        raise ValueError("PoC paused")
    env, job = grading_context()
    try:
        # Freeze grader only after every test answer has a completed terminal receipt.
        for case in env[0]["cases"]:
            for arm in ARMS:
                row = ROOT / "experiment" / ("arm-" + sha(arm)) / ("case-" + sha(case["id"]))
                if _read(row / "answer.json")["status"] != "completed":
                    raise ValueError("Incomplete answer inventory")
        if phase == "prepare-grade":
            prepare(job, list(ARMS))
        elif phase == "grade":
            grade(job, list(ARMS), partition_index=partition)
        else:
            # This PoC omits the old permissive per-answer judge; its only outcome
            # judge is the unchanged strict grader. Do not fabricate unused receipts.
            job.verify()
            for case in env[0]["cases"]:
                for arm in ARMS:
                    row = ROOT / "experiment" / ("arm-" + sha(arm)) / ("case-" + sha(case["id"]))
                    for stage, start in [("packet", "capture"), ("answer", "answer")]:
                        _read(row / (start + "-start.json"))
                        if _read(row / (stage + ".json"))["status"] != "completed":
                            raise ValueError("Incomplete source/answer receipt")
            result = summarize(job, list(ARMS))
            for arm in ARMS:
                test = result["arms"][arm]["test"]
                if test["denominator"] != 100 or test["graded"] != 100 or test["unscored"]:
                    raise ValueError("Incomplete100-case strict grade")
            atomic(ROOT / "strict-summary.json", dump(result).encode())
            protocol = check_protocol()
            for path, digest in protocol["baseline_hashes"].items():
                if sha((OLD / path).read_bytes()) != digest:
                    raise ValueError("Historical baseline drift:" + path)
            atomic(
                ROOT / "completed.json",
                dump(
                    {
                        "verified_at": time.time(),
                        "test_questions": 100,
                        "arms": list(ARMS),
                        "protocol_sha": sha(dump(protocol)),
                        "summary_sha": sha(dump(result)),
                    }
                ).encode(),
            )
            print("Verified both100-case arms and unchanged baseline receipts", flush=True)
    finally:
        env[1].close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=["freeze", "capture", "answer", "prepare-grade", "grade", "verify"])
    p.add_argument("--partition", type=int, default=0)
    p.add_argument("--partitions", type=int, default=4)
    a = p.parse_args()
    if a.phase == "freeze":
        freeze()
    elif a.phase == "capture":
        capture()
    elif a.phase == "answer":
        if not 0 <= a.partition < a.partitions:
            raise ValueError("Invalid partition")
        answer_partition(a.partition, a.partitions)
    else:
        grading(a.phase, a.partition)


if __name__ == "__main__":
    main()
