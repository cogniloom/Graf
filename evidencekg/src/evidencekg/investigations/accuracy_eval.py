"""Offline, human-labelled graph/answer evaluation with explicit unknown denominators.

Collect actual extraction separately. Synthetic seed expectations are not an
independent accuracy assessment; unreviewed labels are never scored.
"""

import argparse
import hashlib
import json
from pathlib import Path

METRICS = (
    "supported_conclusions",
    "contradiction_coverage",
    "appropriate_uncertainty",
    "useful_clarification",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def unique(rows):
    result = {}
    for row in rows:
        if row["case_id"] in result:
            raise ValueError("Duplicate case_id")
        result[row["case_id"]] = row
    return result


def report(corpus, predictions, labels):
    cases = unique(corpus["cases"])
    predicted, reviewed = unique(predictions["cases"]), unique(labels["cases"])
    if set(predicted) - set(cases) or set(reviewed) - set(cases):
        raise ValueError("Foreign evaluation case")
    corpus_sha = digest(corpus)
    if predictions["corpus_sha"] != corpus_sha or labels["corpus_sha"] != corpus_sha:
        raise ValueError("Evaluation corpus identity mismatch")
    for key in ("implementation", "prompt_version", "model", "effort"):
        if not isinstance(predictions.get(key), str) or not predictions[key]:
            raise ValueError(f"Missing provenance: {key}")
    totals = {
        "scheduled": len(cases),
        "recorded": 0,
        "failed": 0,
        "missing": 0,
        "reviewed": 0,
        "unreviewed": 0,
    }
    graph = {
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "reviewed_cases": 0,
        "qualifications_preserved": 0,
        "qualifications_required": 0,
    }
    answers = {metric: {"passed": 0, "assessed": 0, "unknown": 0} for metric in METRICS}
    rows = []
    assessment_scope = []
    for key, case in cases.items():
        observation, label = predicted.get(key), reviewed.get(key)
        state = "missing"
        if observation is None:
            totals["missing"] += 1
        else:
            totals["recorded"] += 1
            if observation.get("source_sha") != digest(case["sources"]):
                raise ValueError(f"Source identity mismatch: {key}")
            if observation.get("status") not in {"ok", "error"}:
                raise ValueError(f"Invalid observation status: {key}")
            if observation["status"] == "error":
                totals["failed"] += 1
                state = "error"
            else:
                state = "unreviewed"
                if label and label.get("reviewer", "").strip():
                    if label.get("observation_sha") != digest(observation):
                        raise ValueError(f"Review is not bound to this observation: {key}")
                    if label.get("source_sha") != digest(case["sources"]):
                        raise ValueError(f"Review source mismatch: {key}")
                    if set(label.get("answer_ratings", {})) - set(METRICS):
                        raise ValueError("Unknown answer metric")
                    state = "reviewed"
                    totals["reviewed"] += 1
                    if label.get("graph_expected") is not None:
                        actual = observation.get("graph_claims")
                        if not isinstance(actual, list) or not isinstance(label["graph_expected"], list):
                            raise ValueError("Graph assessment requires explicit actual and expected arrays")
                        actual_set, expected = (
                            set(map(digest, actual)),
                            set(map(digest, label["graph_expected"])),
                        )
                        graph["true_positive"] += len(actual_set & expected)
                        graph["false_positive"] += len(actual_set - expected)
                        graph["false_negative"] += len(expected - actual_set)
                        graph["reviewed_cases"] += 1
                    qualification = label.get("qualifications", {})
                    for value in qualification.values():
                        if type(value) is not bool:
                            raise ValueError("Qualification ratings must be booleans")
                        graph["qualifications_required"] += 1
                        graph["qualifications_preserved"] += int(value)
        if state != "reviewed":
            totals["unreviewed"] += 1
        for metric, counts in answers.items():
            value = label.get("answer_ratings", {}).get(metric) if state == "reviewed" else None
            if value is None:
                counts["unknown"] += 1
            elif type(value) is not bool:
                raise ValueError("Answer ratings must be booleans or null")
            else:
                if observation.get("answer") is None:
                    raise ValueError("Cannot assess an absent answer")
                counts["assessed"] += 1
                counts["passed"] += int(value)
        assessment_scope.append(
            {
                "case_id": key,
                "graph_expected": label.get("graph_expected") if state == "reviewed" else None,
                "qualification_keys": sorted(label.get("qualifications", {})) if state == "reviewed" else [],
                "answer_metrics": sorted(
                    k for k, v in label.get("answer_ratings", {}).items() if v is not None
                )
                if state == "reviewed"
                else [],
            }
        )
        rows.append(
            {
                "case_id": key,
                "state": state,
                "error": observation.get("error") if observation and state == "error" else None,
            }
        )
    tp, fp, fn = (graph[k] for k in ("true_positive", "false_positive", "false_negative"))
    graph.update(precision=tp / (tp + fp) if tp + fp else None, recall=tp / (tp + fn) if tp + fn else None)
    return {
        "version": 1,
        "corpus_sha": corpus_sha,
        "predictions_sha": digest(predictions),
        "labels_sha": digest(labels),
        "provenance": {k: predictions[k] for k in ("implementation", "prompt_version", "model", "effort")},
        "totals": totals,
        "graph": graph,
        "answers": answers,
        "cases": rows,
        "assessment_scope_sha": digest(assessment_scope),
        "limitation": "Human review attribution is recorded, not independently authenticated. Seed cases are not population accuracy.",
    }


def collect(corpus, directory):
    from evidencekg.config import initialize
    from evidencekg.ingest import ingest
    from evidencekg.knowledge import load, signature

    from .evidence_contract import VERSION

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    records = []
    for index, case in enumerate(corpus["cases"]):
        root = directory / str(index) / "sources"
        root.mkdir(parents=True)
        for n, source in enumerate(case["sources"]):
            (root / f"source-{n}.txt").write_text(source, encoding="utf-8")
        store = initialize(root.parent / "vault", root, {"ocr": "off"})
        record = {"case_id": case["case_id"], "source_sha": digest(case["sources"]), "answer": None}
        try:
            snapshot = ingest(store)
            graph = load(store, snapshot)
            source_names = {
                d["document_version_id"]: Path(d["path"]).name for d in store.manifest(snapshot)["documents"]
            }
            record.update(
                status="ok",
                snapshot_id=snapshot,
                graph_claims=[
                    {
                        k: node.get(k)
                        for k in (
                            "quote",
                            "entity_key",
                            "predicate",
                            "polarity",
                            "modality",
                            "condition",
                            "applicable_on",
                            "attribution",
                            "canonical_start",
                            "canonical_end",
                        )
                    }
                    | {"source": source_names[node["document_version_id"]]}
                    for node in (graph or {}).get("nodes", [])
                    if node["kind"] == "claim"
                ],
                graph_sha=digest(graph),
                coverage=(graph or {}).get("coverage"),
            )
        except Exception as exc:
            record.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            store.close()
        records.append(record)
    return {
        "corpus_sha": digest(corpus),
        "implementation": digest(signature()),
        "prompt_version": VERSION,
        "model": "none-extraction-only",
        "effort": "none",
        "cases": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--collect", type=Path, help="New private directory for real extraction observations")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument(
        "--baseline", type=Path, help="Prior report using identical corpus and reviewed cases"
    )
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text())
    if args.collect:
        result = collect(corpus, args.collect)
    else:
        if not args.predictions or not args.labels:
            parser.error("Scoring requires --predictions and --labels")
        result = report(corpus, json.loads(args.predictions.read_text()), json.loads(args.labels.read_text()))
        if args.baseline:
            baseline = json.loads(args.baseline.read_text())
            if baseline["corpus_sha"] != result["corpus_sha"] or baseline["cases"] != result["cases"]:
                raise ValueError("Baseline and candidate must have identical corpus and case review status")
            if baseline["assessment_scope_sha"] != result["assessment_scope_sha"]:
                raise ValueError("Baseline and candidate assessed items differ")
            if baseline["totals"]["reviewed"] != len(corpus["cases"]):
                raise ValueError("Comparison requires complete reviewed cohorts")
            result = {
                "baseline": baseline,
                "candidate": result,
                "note": "Compare metric denominators; different assessed items are not an accuracy delta.",
            }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
