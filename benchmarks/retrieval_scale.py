"""Local retrieval-policy scale probe; no generative calls or answer accuracy claim."""

import argparse
import json
import math
import random
import statistics
import time
from pathlib import Path

from evidencekg.reference_ranking import ReferenceRanking

from benchmarks.runner import Discovery, digest, verify, write


def measure(dataset, output, repeats=3):
    manifest = verify(dataset)
    questions = json.loads((dataset / "questions.json").read_text())
    started = time.perf_counter()
    graph = Discovery(dataset, "core")
    graph_setup = time.perf_counter() - started
    try:
        started = time.perf_counter()
        lexical = ReferenceRanking(graph.store, graph.snapshot)
        lexical_setup = time.perf_counter() - started
        schedule = [
            (q, repetition, arm)
            for q in questions
            for repetition in range(repeats)
            for arm in ("lexical_policy", "mechanical_policy")
        ]
        random.Random(418).shuffle(schedule)
        rows = []
        for question, repetition, arm in schedule:
            started = time.perf_counter()
            packet = (
                graph.engine.retrieve(question["question"], limit=12)
                if arm == "mechanical_policy"
                else lexical.retrieve(question["question"], "baseline", limit=12)
            )
            seconds = time.perf_counter() - started
            passages = [
                {"path": graph.paths[s["document_version_id"]], "text": s["text"]}
                for s in packet["segments"]
            ]
            rows.append(
                {
                    "case_id": question["id"],
                    "repeat": repetition,
                    "arm": arm,
                    "seconds": seconds,
                    "passages": passages,
                    "selected_text_bytes": sum(
                        len(s["text"].encode()) for s in passages
                    ),
                    "candidates": packet["candidate_counts"],
                    "remaining": packet["omitted_count"],
                }
            )
        # Scoring begins only after all question-only retrieval has completed.
        cases = {c["id"]: c for c in json.loads((dataset / "cases.json").read_text())}
        for row in rows:
            refs = cases[row["case_id"]]["required_evidence"]
            covered = sum(
                any(
                    s["path"] == ref["path"] and ref["quote"] in s["text"]
                    for s in row["passages"]
                )
                for ref in refs
            )
            row.update(
                reference_count=len(refs),
                references_covered=covered,
                complete_reference_set=covered == len(refs),
            )
        result = {
            "scope": "Mechanical versus lexical retrieval policies; not GPT answer accuracy or isolated graph ablation",
            "generative_model_calls": 0,
            "dataset_manifest_sha256": digest((dataset / "manifest.json").read_bytes()),
            "files": manifest["file_count"],
            "lines": manifest["source_lines"],
            "source_bytes": manifest["source_bytes"],
            "index_bytes": manifest["index_bytes"],
            "ingestion_seconds": manifest["ingestion_seconds"],
            "questions": len(questions),
            "repeats": repeats,
            "graph_setup_seconds": graph_setup,
            "lexical_setup_seconds": lexical_setup,
            "cache": "Reused engine; OS caches not flushed; shared host",
            "summary": {},
            "trials": rows,
        }
        for arm in ("lexical_policy", "mechanical_policy"):
            selected = [r for r in rows if r["arm"] == arm]
            times = sorted(r["seconds"] for r in selected)
            result["summary"][arm] = {
                "trials": len(selected),
                "complete_designated_reference_sets": sum(
                    r["complete_reference_set"] for r in selected
                ),
                "designated_reference_recall": sum(
                    r["references_covered"] for r in selected
                )
                / sum(r["reference_count"] for r in selected),
                "median_seconds": statistics.median(times),
                "p95_seconds": times[math.ceil(len(times) * 0.95) - 1],
                "mean_selected_text_bytes": statistics.mean(
                    r["selected_text_bytes"] for r in selected
                ),
            }
        write(output, result)
        return result["summary"]
    finally:
        graph.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    print(json.dumps(measure(args.dataset, args.output, args.repeats), indent=2))
