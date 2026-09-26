"""Journaled scale probe through the actual hybrid runtime; no answering model."""

import argparse
import json
import math
import signal
import statistics
import time
from pathlib import Path

from evidencekg.hybrid.runtime import Runtime, prepare

from benchmarks.runner import digest, verify, write


def measure(dataset, output, models, database_config, timeout=180):
    manifest = verify(dataset)
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    preparation = prepare(dataset / "index", models, database_config, device="cuda")
    preparation["total_preparation_seconds"] = time.perf_counter() - started
    preparation["models_reused"] = True
    preparation["model_download_seconds"] = 0
    write(output / "preparation.json", preparation)
    runtime = Runtime(dataset / "index")
    questions = json.loads((dataset / "questions.json").read_text())
    write(
        output / "intent.json",
        {
            "manifest_sha256": digest((dataset / "manifest.json").read_bytes()),
            "questions": len(questions),
            "timeout_per_query": timeout,
            "runtime_identity": runtime.config,
            "generative_model_calls": 0,
            "scope": "Actual hybrid retrieval; no final-answer accuracy or model-token claim",
        },
    )

    def expired(signum, frame):
        raise TimeoutError("Hybrid query exceeded the declared deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    rows = []
    try:
        for number, question in enumerate(questions):
            write(output / f"query-{number:03}-intent.json", question)
            started = time.perf_counter()
            signal.setitimer(signal.ITIMER_REAL, timeout)
            try:
                result = runtime.retrieve(question["question"], limit=12)
            except Exception as error:
                write(
                    output / f"query-{number:03}-failure.json",
                    {
                        "case_id": question["id"],
                        "seconds": time.perf_counter() - started,
                        "error": type(error).__name__ + ": " + str(error),
                        "automatic_retry": False,
                    },
                )
                raise
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            row = {
                "case_id": question["id"],
                "seconds": time.perf_counter() - started,
                "result": result,
            }
            write(output / f"query-{number:03}.json", row)
            rows.append(row)
            print(question["id"], round(row["seconds"], 3), flush=True)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        runtime.close()
    # Gold is accessed only after question-only retrieval is finished.
    cases = {c["id"]: c for c in json.loads((dataset / "cases.json").read_text())}
    coverage = []
    for row in rows:
        refs = cases[row["case_id"]]["required_evidence"]
        covered = sum(
            any(
                s["source_path"] == ref["path"] and ref["quote"] in s["text"]
                for s in row["result"]["segments"]
            )
            for ref in refs
        )
        coverage.append(
            {
                "case_id": row["case_id"],
                "reference_count": len(refs),
                "covered": covered,
                "complete_designated_set": covered == len(refs),
            }
        )
    times = sorted(r["seconds"] for r in rows)
    summary = {
        "files": manifest["file_count"],
        "lines": manifest["source_lines"],
        "source_bytes": manifest["source_bytes"],
        "ingestion_seconds": manifest["ingestion_seconds"],
        "hybrid_preparation_seconds": preparation["total_preparation_seconds"],
        "query_count": len(rows),
        "median_seconds": statistics.median(times),
        "p95_seconds": times[math.ceil(len(times) * 0.95) - 1],
        "cache_hits": sum(r["result"]["cache"]["hit"] for r in rows),
        "complete_designated_sets": sum(r["complete_designated_set"] for r in coverage),
        "coverage": coverage,
        "generative_model_calls": 0,
        "scope": "Single-pass actual hybrid retrieval of designated references; not answer accuracy. First query includes lazy model load. Existing model weights reused; OS cache not flushed.",
    }
    write(output / "summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    print(
        json.dumps(
            measure(
                args.dataset,
                args.output,
                args.models,
                args.database_config,
                args.timeout,
            ),
            indent=2,
        )
    )
