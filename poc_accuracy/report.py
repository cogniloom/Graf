"""Report actual strict outcomes and operational costs without altering receipts."""

import csv
import io
from collections import Counter
from statistics import mean, median

from evidencekg.db import atomic, dump, sha
from evidencekg.experiments import _read

from .benchmark import ARMS, BASELINES, OLD, ROOT, check_protocol


def outcomes(root, arm):
    folder = root / ("arm-" + sha(arm))
    plan = _read(folder / "plan.json")
    by_id = {item["evaluation_id"]: item for item in plan["items"]}
    result = {item["case_id"]: {"status": "ungraded", "split": item["split"]} for item in plan["items"]}
    for batch in plan["batches"]:
        path = folder / ("batch-" + batch["batch_id"]) / "result.json"
        if not path.exists():
            continue
        receipt = _read(path)
        if receipt["status"] != "graded":
            continue
        for row in receipt["raw_output"]["results"]:
            item = by_id[row["evaluation_id"]]
            result[item["case_id"]] = {"status": "graded", "outcome": row["outcome"], "split": item["split"]}
    return result


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, round((len(values) - 1) * fraction))] if values else None


def main():
    protocol = check_protocol()
    if not (ROOT / "completed.json").exists():
        raise ValueError("Full comparison has not completed")
    original = _read(OLD / "experiment/experiment.json")
    cases = [c for c in original["cases"] if c["split"] == "test"]
    rows = []
    statistics = {}
    for arm in (*BASELINES, *ARMS):
        base = OLD if arm in BASELINES else ROOT
        grades = outcomes(base / ("completion-grading" if arm in BASELINES else "grading"), arm)
        times = []
        answer_seconds = []
        calls = 0
        reference_found = reference_total = bundles_found = 0
        local_windows = 0
        for case in cases:
            grade = grades[case["id"]]
            if grade["status"] != "graded":
                raise ValueError("Incomplete case grade:" + case["id"])
            folder = base / "experiment" / ("arm-" + sha(arm)) / ("case-" + sha(case["id"]))
            packet = _read(folder / "packet.json")
            answer = _read(folder / "answer.json")
            raw = packet["raw_retrieval"]
            seconds = raw.get("timing", {}).get("retrieval_seconds", packet["retrieval_seconds"])
            times.append(seconds)
            answer_seconds.append(answer["elapsed_seconds"])
            calls += (packet.get("retrieval_costs") or {}).get("model_calls", 0)
            wanted = {r["segment_id"] for r in case["evidence_refs"]}
            actual = {s["id"] for s in packet["selected_segments"]}
            found = len(wanted & actual)
            reference_found += found
            reference_total += len(wanted)
            bundles_found += wanted <= actual
            inf = raw.get("local_inference", {})
            local_windows += inf.get("candidate_windows", 0) + inf.get("bundle_windows", 0)
            rows.append(
                {
                    "arm": arm,
                    "case_id": case["id"],
                    "outcome": grade["outcome"],
                    "reference_segments_found": found,
                    "reference_segments_total": len(wanted),
                    "complete_reference_set": wanted <= actual,
                    "retrieval_seconds": seconds,
                    "answer_seconds": answer["elapsed_seconds"],
                }
            )
        counts = Counter(r["outcome"] for r in rows if r["arm"] == arm)
        statistics[arm] = {
            "denominator": 100,
            "successful_answers": counts["answered_correctly"],
            "outcomes": dict(counts),
            "designated_reference_segments": {"found": reference_found, "total": reference_total},
            "complete_designated_reference_sets": bundles_found,
            "retrieval_seconds": {
                "sum": sum(times),
                "mean": mean(times),
                "median": median(times),
                "p95": percentile(times, 0.95),
            },
            "answer_seconds_sum": sum(answer_seconds),
            "retrieval_subscription_calls": calls,
            "local_rerank_windows": local_windows,
        }
    by = {(r["arm"], r["case_id"]): r for r in rows}
    pairs = {}
    for control, treatment in [("optimized_mechanical_v2", ARMS[0]), (ARMS[0], ARMS[1])]:
        gains = []
        losses = []
        for case in cases:
            left = by[control, case["id"]]["outcome"] == "answered_correctly"
            right = by[treatment, case["id"]]["outcome"] == "answered_correctly"
            if right and not left:
                gains.append(case["id"])
            if left and not right:
                losses.append(case["id"])
        pairs[control + " -> " + treatment] = {"gained": gains, "lost": losses}
    result = {
        "protocol_sha": sha(dump(protocol)),
        "statistics": statistics,
        "paired": pairs,
        "index": protocol["index_status"],
        "limitations": protocol["limitations"],
        "reference_recall_scope": "Designated reference sets are not exhaustive relevance labels or human-adjudicated evidence bundles.",
        "efficiency_scope": "Recorded retrieval times exclude one-time index construction/model loading. Historical timings and concurrent current runs are not equal-load throughput benchmarks. Local inference is not free; GPU work and setup reported separately.",
    }
    atomic(ROOT / "comparison.json", dump(result).encode())
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic(ROOT / "per-question.csv", stream.getvalue().encode())
    lines = [
        "# Isolated accuracy PoC: original100 questions /1000 root documents",
        "",
        "Repeated frozen benchmark; model-authored references and automated strict grading. No production implementation changes.",
        "",
        "| Method | Supported correct /100 | Complete designated reference sets /100 | Median retrieval seconds | Retrieval subscription calls |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm, s in statistics.items():
        lines.append(
            f"| {arm} | {s['successful_answers']} | {s['complete_designated_reference_sets']} | {s['retrieval_seconds']['median']:.3f} | {s['retrieval_subscription_calls']} |"
        )
    lines += [
        "",
        f"Final index construction: {protocol['index_status']['index_seconds']:.2f} seconds. "
        f"{protocol['index_status']['segments']} nonempty segments, {protocol['index_status']['windows']} indexed windows.",
        "",
    ]
    for name, pair in pairs.items():
        lines.append(f"- {name}: {len(pair['gained'])} gained, {len(pair['lost'])} lost.")
    lines += [
        "",
        *protocol["limitations"],
        "",
        result["reference_recall_scope"],
        "",
        result["efficiency_scope"],
        "",
        "See comparison.json and per-question.csv for auditable denominators, timing and paired changes.",
    ]
    atomic(ROOT / "REPORT.md", "\n".join(lines).encode())
    print(dump({arm: s["successful_answers"] for arm, s in statistics.items()}), flush=True)


if __name__ == "__main__":
    main()
