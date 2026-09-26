"""Reproducible private sampling and paired retrieval evaluation primitives.

Evaluation material belongs in a private state directory, never package examples.
Candidate retrieval and model answer quality are deliberately separate metrics.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from .db import atomic, dump, sha
from .inventory import capture, inventory

STRATA = {
    "email": {".eml"},
    "pdf": {".pdf"},
    "word": {".docx"},
    "image": {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"},
    "text": {".txt", ".md"},
}


def stratum(path):
    suffix = Path(path).suffix.lower()
    return next((kind for kind, extensions in STRATA.items() if suffix in extensions), "unsupported")


def sample_corpus(root, output, seed="2026-09-25", counts=None):
    """Freeze full path inventory and capture a ranked random sample without replacement.

    SHA-256(seed, relative path) ranks each format stratum. Unsupported/unreadable
    draws stay in the manifest and are never replaced by easier examples.
    """
    root, output = Path(root).resolve(strict=True), Path(output).resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Sample destination must be new and outside source corpus")
    counts = counts or {"email": 16, "pdf": 8, "word": 3, "image": 3, "text": 2, "unsupported": 2}
    if set(counts) - (set(STRATA) | {"unsupported"}) or any(
        type(n) is not int or n < 0 for n in counts.values()
    ):
        raise ValueError("Invalid stratum counts")
    entries, errors = inventory(root, [])
    output.mkdir(parents=True, mode=0o700)
    captured = output / "sources"
    captured.mkdir(mode=0o700)
    selected = []
    for kind, count in sorted(counts.items()):
        group = [e for e in entries if stratum(e["path"]) == kind]
        group.sort(key=lambda e: (sha(dump([seed, e["path"]])), e["path"]))
        for item in group[:count]:
            record = {**item, "stratum": kind, "selection_rank": sha(dump([seed, item["path"]]))}
            try:
                if item["status"] != "pending":
                    raise ValueError(item["warning"])
                data = capture(root, item["path"], 100_000_000)
                atomic(captured / item["path"], data)
                record.update(capture_status="captured", sha256=sha(data), byte_length=len(data))
            except (OSError, ValueError) as exc:
                record.update(capture_status="failed", error=str(exc))
            selected.append(record)
    result = {
        "source_root": str(root),
        "seed": seed,
        "method": "sha256(seed,relative_path) ranking without replacement within declared format strata",
        "requested_counts": counts,
        "population_known": len(entries),
        "inventory_complete": not errors,
        "inventory_errors": errors,
        "population_strata": dict(Counter(stratum(e["path"]) for e in entries)),
        "population_inventory_sha": sha(dump(entries)),
        "selected": selected,
        "scope": "format-stratified sample, not representative prevalence or full-dossier analysis",
    }
    atomic(output / "population.json", dump(entries).encode())
    atomic(output / "sample.json", dump(result).encode())
    return result


def load_cases(path, api, snapshot_id):
    """Admit hand/independently curated questions only with exact source-span labels."""
    cases = json.loads(Path(path).read_text())
    ids = set()
    from .validation import validate_refs

    for case in cases:
        if not case.get("id") or case["id"] in ids or not case.get("question", "").strip():
            raise ValueError("Unique case IDs and a question required")
        ids.add(case["id"])
        refs = case.get("evidence_refs", [])
        if not refs or not case.get("expected_answer", "").strip():
            raise ValueError("Source-validated reference answer required")
        segments = {r["segment_id"]: api.segment(snapshot_id, r["segment_id"]) for r in refs}
        validate_refs(refs, segments)
    return cases


def paired_summary(rows):
    """No significance claim: report paired outcomes with denominators and Wilson CIs."""
    groups = {}
    for row in rows:
        key = (row["case_id"], row["arm"])
        if key in groups:
            raise ValueError("Duplicate case/arm result")
        groups[key] = row
    arms = sorted({r["arm"] for r in rows})
    cases = sorted({r["case_id"] for r in rows})
    if any((case, arm) not in groups for case in cases for arm in arms):
        raise ValueError("Incomplete pairs; cannot silently drop failed/missing arms")
    result = {
        "case_count": len(cases),
        "arms": {},
        "scope": "small paired benchmark; not whole-corpus recall",
    }
    for arm in arms:
        values = [groups[(case, arm)] for case in cases]
        n = len(values)
        successes = sum(v.get("correct") is True for v in values)
        z = 1.96
        center = (successes / n + z * z / (2 * n)) / (1 + z * z / n) if n else 0
        radius = (
            z * math.sqrt(successes / n * (1 - successes / n) / n + z * z / (4 * n * n)) / (1 + z * z / n)
            if n
            else 0
        )
        result["arms"][arm] = {
            "correct": successes,
            "denominator": n,
            "missing_or_invalid": sum(v.get("valid") is not True for v in values),
            "wilson_95_interval": [max(0, center - radius), min(1, center + radius)] if n else None,
            "target_segments_retrieved": sum(v.get("target_segments_retrieved", 0) for v in values),
            "target_segments_total": sum(v.get("target_segments_total", 0) for v in values),
        }
    return result
