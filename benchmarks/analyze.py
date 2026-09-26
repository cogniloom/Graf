"""Quality-conditioned analysis and blinded human-review export of verified runs."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from pathlib import Path

from benchmarks.runner import (
    ARMS,
    digest,
    encoded,
    interval,
    load_trials,
    report,
    write,
)


def analyze(directory):
    summary = report(
        directory
    )  # Validate raw measurements before deriving comparisons.
    config = json.loads((directory / "run.json").read_text())
    paths, rows = load_trials(directory, config)
    scores = {
        s["trial"]: s
        for p in sorted((directory / "grading").glob("score-*.json"))
        for s in [json.loads(p.read_text())]
    }
    for path, row in zip(paths, rows):
        row["strict_pass"] = scores.get(path.parent.name, {}).get("pass", False)
    complete = (
        len(rows) == len(config["schedule"])
        and all(r["usage_complete"] for r in rows)
        and all(
            r["status"] != "answered" or p.parent.name in scores
            for p, r in zip(paths, rows)
        )
    )
    result = {
        "run_sha256": digest((directory / "run.json").read_bytes()),
        "complete_measured_and_graded": complete,
        "quality": "strict automated judgment; human adjudication pending",
        "both_strict_pass_pairs": 0,
        "paired": {},
        "arms": {},
    }
    if not complete:
        result["reason"] = (
            "Incomplete measurements or grading; comparative claims withheld"
        )
        return result
    paired = {}
    for row in rows:
        paired.setdefault((row["case_id"], row["repeat"]), {})[row["arm"]] = row
    if any(set(pair) != set(ARMS) for pair in paired.values()):
        raise ValueError("Incomplete pair")
    result["both_strict_pass_pairs"] = sum(
        all(r["strict_pass"] for r in pair.values()) for pair in paired.values()
    )
    # Bootstrap over questions, not the larger count of repeated trials.
    metrics = {
        "all_scheduled_total_tokens_saved": lambda a, b: sum(
            a["usage"][k] - b["usage"][k] for k in ("input_tokens", "output_tokens")
        ),
        "all_scheduled_strict_accuracy_delta_graf_minus_files": lambda a, b: (
            int(b["strict_pass"]) - int(a["strict_pass"])
        ),
        "both_correct_seconds_saved": lambda a, b: a["seconds"] - b["seconds"],
        "both_correct_total_tokens_saved": lambda a, b: sum(
            a["usage"][k] - b["usage"][k] for k in ("input_tokens", "output_tokens")
        ),
    }
    for name, metric in metrics.items():
        by_question = {}
        for (case_id, _), pair in paired.items():
            if name.startswith("both_correct") and not all(
                r["strict_pass"] for r in pair.values()
            ):
                continue
            by_question.setdefault(case_id, []).append(
                metric(pair["files"], pair["graf"])
            )
        values = [statistics.mean(v) for v in by_question.values()]
        result["paired"][name] = {
            "questions": len(values),
            "mean": statistics.mean(values) if values else None,
            "mean_95pct_cluster_bootstrap": interval(values),
        }
    for arm in ARMS:
        selected = [r for r in rows if r["arm"] == arm]
        passes = sum(r["strict_pass"] for r in selected)
        tokens = sum(
            r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in selected
        )
        result["arms"][arm] = {
            "scheduled": len(selected),
            "strict_passes": passes,
            "total_tokens": tokens,
            "mean_total_tokens": tokens / len(selected),
            "tokens_per_strict_pass_including_failed_answers": tokens / passes
            if passes
            else None,
            "total_seconds": sum(r["seconds"] for r in selected),
            "strict_accuracy": passes / len(selected),
            "categories": {
                category: {
                    "trials": sum(r["category"] == category for r in selected),
                    "passes": sum(
                        r["category"] == category and r["strict_pass"] for r in selected
                    ),
                }
                for category in sorted({r["category"] for r in selected})
            },
        }
    delta = result["paired"]["both_correct_seconds_saved"]["mean"]
    setup = config["ingestion_seconds"] + config["discovery_setup_seconds"]
    result["core_setup_break_even_queries_on_both_correct_subset"] = (
        math.ceil(setup / delta)
        if config["backend"] == "core" and delta is not None and delta > 0
        else None
    )
    result["setup_limit"] = (
        "Core only; hybrid preparation/download cost is not inferred. Corpus export cost excluded for both."
    )
    result["publication_ready"] = summary["publication_ready"]
    return result


def export_review(directory, output):
    """Keep the reviewer packet arm-blind; retain the join key separately."""
    report(directory)
    config = json.loads((directory / "run.json").read_text())
    dataset = Path(config["dataset"])
    cases = {c["id"]: c for c in json.loads((dataset / "cases.json").read_text())}
    paths, rows = load_trials(directory, config)
    entries = list(zip(paths, rows))
    random.Random(701).shuffle(entries)
    output.mkdir(parents=True, exist_ok=False)
    packet, key = [], []
    for number, (path, row) in enumerate(entries):
        review_id = f"response-{number:04}"
        case = cases[row["case_id"]]
        source_paths = {r["path"] for r in case["required_evidence"]}
        source_paths |= {c["path"] for c in row.get("answer", {}).get("citations", [])}
        sources = {}
        for name in sorted(source_paths):
            source = (dataset / "sources" / name).resolve()
            if (
                source.is_relative_to((dataset / "sources").resolve())
                and source.is_file()
            ):
                sources[name] = source.read_text()
        packet.append(
            {
                "review_id": review_id,
                "question": case["question"],
                "reference_answer": case["expected_answer"],
                "answerable": case["answerable"],
                "candidate": row.get("answer"),
                "source_documents": sources,
                "review": {
                    "correct": None,
                    "complete": None,
                    "supported": None,
                    "rationale": "",
                    "reviewer": "",
                },
            }
        )
        key.append(
            {
                "review_id": review_id,
                "trial": path.parent.name,
                "result_sha256": digest(path.read_bytes()),
                "arm": row["arm"],
            }
        )
    write(output / "reviewer-packet.json", packet)
    write(output / "coordinator-key-do-not-share.json", key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--review-output", type=Path)
    args = parser.parse_args()
    if args.review_output:
        export_review(args.run, args.review_output)
    else:
        result = analyze(args.run)
        (args.run / "quality-analysis.json").write_text(encoded(result))
        print(encoded(result))


if __name__ == "__main__":
    main()
