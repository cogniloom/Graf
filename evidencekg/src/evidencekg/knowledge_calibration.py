"""Reference-backed fidelity calibration, never self-labelled claim truth.

The caller supplies independently checked reference outcomes with disjoint
source-family IDs for fitting and validation. No incoming document is annotated
or reviewed by this module. An unknown/mismatched scope remains unknown.
"""

from __future__ import annotations

import math
import re

from .db import dump, sha


def checked(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("Nonempty independent reference records are required")
    for row in rows:
        score, correct = row.get("raw_score"), row.get("correct")
        if (
            type(score) not in (int, float)
            or not math.isfinite(score)
            or not 0 <= score <= 1
            or type(correct) is not bool
            or not isinstance(row.get("source_family"), str)
            or not row["source_family"]
        ):
            raise ValueError("Reference rows require raw_score, boolean correct and source_family")
    return rows


def estimate(model, score, scope):
    if scope != model.get("scope") or type(score) not in (int, float) or not math.isfinite(score):
        return None
    if not model["score_range"][0] <= score <= model["score_range"][1]:
        return None
    blocks = model["blocks"]
    # Stepwise isotonic mapping: no unsupported extrapolation outside fit range.
    for block in blocks:
        if score <= block["upper_score"]:
            return block["correct"] / block["count"]
    return blocks[-1]["correct"] / blocks[-1]["count"]


def validate_model(model):
    """Configured files are data, not authority to bypass probability invariants."""
    try:
        scope, blocks, limits, validation = (
            model["scope"],
            model["blocks"],
            model["score_range"],
            model["validation"],
        )
        if (
            model["method"] != "isotonic-pava-v1"
            or not isinstance(blocks, list)
            or not blocks
            or len(blocks) > 100000
        ):
            raise ValueError()
        if (
            set(scope) != {"model_sha256", "language", "input_domain", "probability_target"}
            or scope["language"] not in {"en", "de"}
            or scope["probability_target"] != "transcription_fidelity"
        ):
            raise ValueError()
        if (
            not re.fullmatch(r"[0-9a-f]{64}", scope["model_sha256"])
            or not re.fullmatch(r"[0-9a-f]{64}", model["reference_sha256"])
            or not isinstance(scope["input_domain"], str)
            or not scope["input_domain"]
        ):
            raise ValueError()
        if (
            len(limits) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in limits)
            or limits[0] > limits[1]
        ):
            raise ValueError()
        previous, probability, count = -1.0, -1.0, 0
        for block in blocks:
            upper, correct, total = block["upper_score"], block["correct"], block["count"]
            if type(total) is not int or type(correct) is not int or total <= 0 or not 0 <= correct <= total:
                raise ValueError()
            if (
                type(upper) not in (int, float)
                or not math.isfinite(upper)
                or not limits[0] <= upper <= limits[1]
                or upper <= previous
                or correct / total < probability
            ):
                raise ValueError()
            previous, probability, count = upper, correct / total, count + total
        if previous != limits[1] or count != model["training_count"]:
            raise ValueError()
        if (
            any(
                type(validation[key]) is not int or validation[key] < 0
                for key in ("evaluated", "out_of_range")
            )
            or validation["evaluated"] + validation["out_of_range"] != model["validation_count"]
        ):
            raise ValueError()
        for key in ("raw_brier", "calibrated_brier"):
            value = validation[key]
            if validation["evaluated"] == 0:
                if value is not None:
                    raise ValueError()
            elif type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError()
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("Invalid fidelity calibration artifact") from exc


def fit(training, validation, scope):
    training, validation = checked(training), checked(validation)
    if not isinstance(scope, dict) or set(scope) != {
        "model_sha256",
        "language",
        "input_domain",
        "probability_target",
    }:
        raise ValueError("Calibration needs model, language, input domain and explicit probability target")
    if (
        scope["language"] not in {"en", "de"}
        or scope["probability_target"] != "transcription_fidelity"
        or any(not isinstance(v, str) or not v for v in scope.values())
        or not re.fullmatch(r"[0-9a-f]{64}", scope["model_sha256"])
    ):
        raise ValueError("Only scoped English/German transcription-fidelity calibration is supported")
    if {r["source_family"] for r in training} & {r["source_family"] for r in validation}:
        raise ValueError("Training and validation source families must be disjoint")
    grouped = {}
    for row in training:
        block = grouped.setdefault(row["raw_score"], dict(upper_score=row["raw_score"], correct=0, count=0))
        block["correct"] += row["correct"]
        block["count"] += 1
    blocks = []
    for score in sorted(grouped):
        blocks.append(grouped[score])
        while (
            len(blocks) >= 2
            and blocks[-2]["correct"] / blocks[-2]["count"] > blocks[-1]["correct"] / blocks[-1]["count"]
        ):
            right, left = blocks.pop(), blocks.pop()
            blocks.append(
                dict(
                    upper_score=right["upper_score"],
                    correct=left["correct"] + right["correct"],
                    count=left["count"] + right["count"],
                )
            )
    model = dict(
        method="isotonic-pava-v1",
        scope=scope,
        blocks=blocks,
        score_range=[min(grouped), max(grouped)],
        reference_sha256=sha(dump([training, validation])),
        training_count=len(training),
        validation_count=len(validation),
    )
    evaluated = [(r, estimate(model, r["raw_score"], scope)) for r in validation]
    available = [(r, p) for r, p in evaluated if p is not None]
    raw = sum((r["raw_score"] - r["correct"]) ** 2 for r, _ in available)
    calibrated = sum((p - r["correct"]) ** 2 for r, p in available)
    model["validation"] = dict(
        evaluated=len(available),
        out_of_range=len(validation) - len(available),
        raw_brier=raw / len(available) if available else None,
        calibrated_brier=calibrated / len(available) if available else None,
        status="reference_evaluated_not_deployment_certified",
    )
    return model


def apply(model, score, scope):
    validate_model(model)
    value = estimate(model, score, scope)
    validation = model.get("validation", {})
    if not validation.get("evaluated") or validation.get("calibrated_brier", math.inf) > validation.get(
        "raw_brier", -math.inf
    ):
        value = None
    return dict(
        probability_target="transcription_fidelity",
        raw_score=score,
        calibrated_probability=value,
        calibration_status="reference_estimate" if value is not None else "unvalidated_for_this_input",
        calibration_scope=model["scope"],
        reference_sha256=model["reference_sha256"],
        validation=model["validation"],
        claim_truth=None,
    )


def main():
    """python -m evidencekg.knowledge_calibration references.json output.json"""
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Fit/evaluate independent EN/DE speech reference scores")
    parser.add_argument("references", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.references.read_text())
    result = fit(source["training"], source["validation"], source["scope"])
    with args.output.open("x") as stream:
        stream.write(dump(result) + "\n")


if __name__ == "__main__":
    main()
