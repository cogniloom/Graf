"""Bounded semantic review over immutable attributed artifacts, never graph edges."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict

from .db import dump, ident, sha
from .validation import STAGE_SCHEMAS

VERSION = "semantic-assurance-v4"
INSTRUCTIONS = (
    " Each segment includes citation_spans with exact segment-relative character offsets and verbatim quotes. "
    "Prefer copying these precomputed spans with the segment id and extraction_id; do not estimate offsets "
    "or use document-relative char_start. Several spans may be cited together to retain context. "
    " Produce structured observations as attributed interpretations with exact citations. Normalize issue/event "
    "keys in English across German/English wording; provide semantic query terms in both source language and English, "
    "categories, actors, action/event, "
    "obligations, conditions and negations. Distinguish event dates from document dates. "
    "Critical wording review is independent and blinded: examine negation, conditions, exceptions, dates, "
    "amounts and scope from original evidence without any earlier review. Complete every supplied marker "
    "check, and look beyond this incomplete lexical inventory. In joint reviews read all original passages. "
    "Reconciliation compares both attributed reviews against the original source; flag differences and "
    "missed significance. Challenge each supplied target against all its source premises, looking for "
    "unsupported inference, alternative explanations, qualifiers and disconfirming evidence. Assess each "
    "target exactly once. High impact, unsupported, uncertain or conflicting conclusions require human review. "
    "Only critical_wording returns wording_checks for its top-level risk_markers. Reconciliation and challenge "
    "return assessments only for their top-level targets; never copy nested prior-review checks into the new result. "
    "Candidate associations are hypotheses, never established relationships or mechanical graph edges."
)


def citation_spans(text, maximum=400):
    """Complete, bounded, verbatim citation choices; never normalize evidence.

    Prefer line/sentence boundaries, retaining whitespace. Long blocks split at
    whitespace or at the hard character bound. The choices partition the text;
    they neither select relevant wording nor constrain what the model may cite.
    """
    if type(maximum) is not int or maximum < 1:
        raise ValueError("Positive citation span bound required")
    result, start = [], 0
    while start < len(text):
        end = min(start + maximum, len(text))
        window = text[start:end]
        boundaries = list(re.finditer(r"\n|(?<=[.!?;])\s", window))
        if end < len(text):
            if boundaries:
                end = start + boundaries[-1].end()
            else:
                spaces = list(re.finditer(r"\s", window))
                if spaces:
                    end = start + spaces[-1].end()
        result.append({"start": start, "end": end, "quote": text[start:end]})
        start = end
    return result


BLIND_SPOTS = [
    "Lexical markers cover selected English and German expressions, not every language or paraphrase.",
    "Semantic candidate scheduling uses one discovery wave; later observations are exposed in read-only previews.",
    "Structured facets and semantic query terms depend on model interpretation and may miss or conflate issues.",
    "Anchored groups do not examine every pair; independent calls may share model biases.",
    "Text-only review cannot certify visual/OCR accuracy, comprehension, legal significance or semantic completeness.",
]
PATTERNS = {
    "negation": r"\b(?:not|no|never|neither|without|nicht|kein\w*|ohne)\b",
    "condition": r"\b(?:if|unless|provided|subject to|wenn|sofern|falls)\b",
    "exception": r"\b(?:except|excluding|however|but|ausser|außer|jedoch)\b",
    "date": r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|before|after|until|deadline|bis|vor|nach)\b",
    "amount": r"(?:\b(?:CHF|EUR|USD)\s*\d[\d.,'’]*|[$€£]\s*\d[\d.,]*|\b\d+(?:[.,]\d+)?\s*%)",
    "scope": r"\b(?:only|all|each|any|solely|limited|must|shall|nur|alle|muss)\b",
}


def contract():
    from pathlib import Path

    from . import citation_choices

    return {
        "version": VERSION,
        "instructions_sha": sha(INSTRUCTIONS),
        "schema_sha": sha(dump(STAGE_SCHEMAS)),
        "markers_sha": sha(dump(PATTERNS)),
        "citation_selection_version": "immutable-citation-choice-v2",
        "citation_choices_sha": sha(Path(citation_choices.__file__).read_bytes()),
        "citation_span_chars": 400,
    }


def risk_markers(segment):
    markers = []
    for category, pattern in PATTERNS.items():
        for match in re.finditer(pattern, segment["text"], re.I):
            markers.append(
                {
                    "id": ident("W", segment["id"], category, match.start(), match.end()),
                    "category": category,
                    "segment_id": segment["id"],
                    "start": match.start(),
                    "end": match.end(),
                    "quote": match.group(),
                }
            )
    return sorted(markers, key=lambda m: (m["start"], m["category"]))


def normalize(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def _tasks(store, run_id):
    return [
        {
            **row,
            "payload": json.loads(store.get(row["input_manifest_sha"])),
            "result": json.loads(store.get(row["result_sha"])) if row["result_sha"] else None,
        }
        for row in store.rows("SELECT * FROM review_tasks WHERE run_id=? ORDER BY id", (run_id,))
    ]


def _events(store, run_id):
    return [
        json.loads(r["payload_json"])
        for r in store.rows(
            "SELECT payload_json FROM audit_events WHERE run_id=? AND event_type='assurance_phase' ORDER BY sequence",
            (run_id,),
        )
    ]


def _observations(tasks):
    return [
        {
            "id": ident("O", t["id"], i),
            "task_id": t["id"],
            "worker_id": t["worker_id"],
            "input_sha": t["input_manifest_sha"],
            "result_sha": t["result_sha"],
            "observation": obs,
        }
        for t in tasks
        if t["result"]
        for i, obs in enumerate(t["result"].get("observations", []))
    ]


def _build_discovery(queue, run, tasks):
    segments = queue._segments(run["snapshot_id"])
    by_id = {s["id"]: s for s in segments}
    observations = _observations(tasks)
    groups = defaultdict(lambda: {"members": set(), "observation_ids": set()})
    issues = set()
    normalized_text = {sid: normalize(s["text"]) for sid, s in by_id.items()}
    token_index = defaultdict(set)
    for sid, text in normalized_text.items():
        for token in set(text.split()):
            token_index[token].add(sid)
    for record in observations:
        obs = record["observation"]
        sources = {r["segment_id"] for r in obs["sources"]} & by_id.keys()
        for field in ("issue_keys", "event_keys", "categories", "semantic_query_terms"):
            for value in obs[field]:
                key = normalize(value)
                if not key:
                    continue
                facet = field + ":" + key
                groups[facet]["members"].update(sources)
                groups[facet]["observation_ids"].add(record["id"])
                if field == "issue_keys":
                    issues.add(key)
                if field == "semantic_query_terms":
                    postings = [token_index.get(token, set()) for token in key.split()]
                    matches = set.intersection(*postings) if postings else set()
                    for sid in sorted(matches):
                        if " " + key + " " in " " + normalized_text[sid] + " ":
                            groups[facet]["members"].add(sid)
    candidates = []
    group_records = []
    for facet, group in sorted(groups.items()):
        members = sorted(group["members"])
        group_id = ident("H", run["id"], facet)
        group_records.append(
            {
                "id": group_id,
                "facet": facet,
                "member_segment_ids": members,
                "observation_ids": sorted(group["observation_ids"]),
            }
        )
        for offset, sid in enumerate(members[1:]):
            candidates.append(
                {
                    "id": ident("K", group_id, sid),
                    "group_id": group_id,
                    "facet": facet,
                    "anchor_segment_id": members[0],
                    "member_segment_id": sid,
                    "membership_offset": offset + 1,
                    "group_member_count": len(members),
                }
            )
    artifact = {
        "algorithm": VERSION,
        "observations": observations,
        "groups": group_records,
        "candidates": candidates,
        "source_segment_ids": sorted(by_id),
        "issues": sorted(issues),
        "coverage": "All normalized facet memberships; anchored linear groups, not all pairs.",
        "blind_spots": BLIND_SPOTS,
    }
    return artifact


def discovery_preview(store, run_id):
    """Read-only candidate index from validated results, including partially drained runs."""
    from .review_queue import Queue

    queue = Queue(store)
    return _build_discovery(queue, queue.run(run_id), _tasks(store, run_id))


def _discover(queue, run, tasks):
    artifact = _build_discovery(queue, run, tasks)
    candidates, issues = artifact["candidates"], artifact["issues"]
    segments = queue._segments(run["snapshot_id"])
    by_id = {s["id"]: s for s in segments}
    artifact_sha = queue.store.put(dump(artifact))
    for offset, candidate in enumerate(candidates):
        queue._add(
            run,
            "D",
            "semantic_candidate",
            [by_id[candidate["anchor_segment_id"]], by_id[candidate["member_segment_id"]]],
            {
                "candidate": candidate,
                "discovery_sha": artifact_sha,
                "offset": offset,
                "candidate_count": len(candidates),
                "round": 1,
            },
            candidate["id"],
        )
    # Each issue is independently reconsidered against every source, even absent a candidate.
    for issue in sorted(issues):
        for seg in segments:
            queue._add(
                run,
                "D",
                "issue_reconsideration",
                [seg],
                {"issue": issue, "round": 1, "discovery_sha": artifact_sha},
                issue + ":" + seg["id"],
            )
    return artifact_sha


def _reconcile(queue, run, tasks):
    for seg in queue._segments(run["snapshot_id"]):
        reviews = [
            t
            for t in tasks
            if t["kind"] in {"source", "critical_wording"} and t["payload"]["segments"][0]["id"] == seg["id"]
        ]
        target = {
            "id": ident("X", run["id"], seg["id"]),
            "premise_segment_ids": [seg["id"]],
            "reviews": [
                {
                    "task_id": t["id"],
                    "worker_id": t["worker_id"],
                    "result_sha": t["result_sha"],
                    "result": t["result"],
                }
                for t in reviews
            ],
        }
        queue._add(run, "E", "source_reconciliation", [seg], {"targets": [target], "round": 10}, seg["id"])


def _targets(tasks):
    return [
        {
            "id": ident("J", t["id"], field, i),
            "origin_task_id": t["id"],
            "origin_result_sha": t["result_sha"],
            "worker_id": t["worker_id"],
            "type": field,
            "claim": claim,
            "premise_segment_ids": sorted({r["segment_id"] for r in refs}),
        }
        for t in tasks
        if t["result"] and t["kind"] != "premise_challenge"
        for field in ("findings", "interpretations", "observations")
        for i, claim in enumerate(t["result"].get(field, []))
        for refs in [
            claim.get("sources", claim.get("evidence_refs", []))
            + [ref for date in claim.get("dates", []) for ref in date["sources"]]
        ]
    ]


def schedule(queue, run):
    """Advance at most one finite assurance phase after prior work settles."""
    if not json.loads(run["runner_config_json"]).get("enhanced"):
        return False
    tasks = _tasks(queue.store, run["id"])
    if any(t["state"] != "validated" for t in tasks):
        return False
    phases = {e["phase"] for e in _events(queue.store, run["id"])}
    artifact_sha = None
    if "discovery" not in phases:
        phase = "discovery"
        artifact_sha = _discover(queue, run, tasks)
    elif "reconciliation" not in phases:
        phase = "reconciliation"
        _reconcile(queue, run, tasks)
    elif "challenge" not in phases:
        phase = "challenge"
        for target in _targets(tasks):
            passages = [queue.api.segment(run["snapshot_id"], sid) for sid in target["premise_segment_ids"]]
            queue._add(
                run, "F", "premise_challenge", passages, {"targets": [target], "round": 10}, target["id"]
            )
    else:
        return False
    queue.store.audit("assurance_phase", {"phase": phase, "artifact_sha": artifact_sha}, run["id"])
    # Empty phases still need advancement; only three milestones are possible.
    if not queue.store.rows(
        "SELECT id FROM review_tasks WHERE run_id=? AND state!='validated'", (run["id"],)
    ):
        return schedule(queue, run)
    return True


def _flags(tasks):
    flags = []
    assessments = {
        a["target_id"]: a
        for t in tasks
        if t["result"] and t["kind"] == "premise_challenge"
        for a in t["result"].get("assessments", [])
    }
    for target in _targets(tasks):
        assessment = assessments.get(target["id"])
        if not assessment or assessment["verdict"] != "supported" or assessment["impact"] == "high":
            flags.append(
                {
                    "target_id": target["id"],
                    "reason": "unreviewed_conclusion"
                    if not assessment
                    else assessment["verdict"] + ":" + assessment["impact"],
                    "human_review_required": True,
                }
            )
    reviews = defaultdict(dict)
    for task in tasks:
        if task["result"] and task["kind"] in {"source", "critical_wording"}:
            result = task["result"]
            reviews[task["payload"]["segments"][0]["id"]][task["kind"]] = {
                k: result.get(k, [])
                for k in ("findings", "interpretations", "observations", "unresolved_questions")
            }
        if task["result"] and task["kind"] != "premise_challenge":
            for assessment in task["result"].get("assessments", []):
                if assessment["verdict"] != "supported" or assessment["impact"] == "high":
                    flags.append(
                        {
                            "task_id": task["id"],
                            "target_id": assessment["target_id"],
                            "reason": assessment["verdict"] + ":" + assessment["impact"],
                            "human_review_required": True,
                        }
                    )
    for sid, results in reviews.items():
        if set(results) != {"source", "critical_wording"} or results["source"] != results["critical_wording"]:
            flags.append(
                {
                    "segment_id": sid,
                    "reason": "blind_review_difference_or_missing",
                    "human_review_required": True,
                }
            )
    return flags


def assurance_data(store, run_id, offset=0, limit=100):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Require nonnegative offset and limit 1..1000")
    cfg = json.loads(
        store.one("SELECT runner_config_json FROM runs WHERE id=?", (run_id,))["runner_config_json"]
    )
    if not cfg.get("enhanced"):
        return {
            "items": [],
            "total": 0,
            "offset": offset,
            "next_offset": None,
            "artifacts": [],
            "blind_spots": [],
            "enhanced": False,
        }
    tasks = _tasks(store, run_id)
    events = _events(store, run_id)
    discovery = next(
        (json.loads(store.get(e["artifact_sha"])) for e in events if e["phase"] == "discovery"), {}
    )
    records = (
        [{"type": "observation", **r} for r in _observations(tasks)]
        + [{"type": "candidate", **r} for r in discovery.get("candidates", [])]
        + [{"type": "human_review", **r} for r in _flags(tasks)]
    )
    return {
        "items": records[offset : offset + limit],
        "total": len(records),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(records) else None,
        "artifacts": events,
        "blind_spots": BLIND_SPOTS,
    }


def assurance_status(store, run_id):
    cfg = json.loads(
        store.one("SELECT runner_config_json FROM runs WHERE id=?", (run_id,))["runner_config_json"]
    )
    if not cfg.get("enhanced"):
        return {"enhanced": False}
    tasks = _tasks(store, run_id)
    counts = {}
    for kind in STAGE_SCHEMAS:
        selected = [t for t in tasks if t["kind"] == kind]
        counts[kind] = {
            "scheduled": len(selected),
            "validated": sum(t["state"] == "validated" for t in selected),
        }
    flags = _flags(tasks)
    return {
        "enhanced": True,
        "version": cfg["assurance_contract"]["version"],
        "stages": counts,
        "phases": [e["phase"] for e in _events(store, run_id)],
        "structured_observations": len(_observations(tasks)),
        "human_review_required": bool(flags),
        "human_review_flags": len(flags),
        "semantic_complete": False,
        "blind_spots": BLIND_SPOTS,
    }
