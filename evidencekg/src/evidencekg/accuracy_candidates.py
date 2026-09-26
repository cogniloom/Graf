"""Read-only, deterministic source candidates for a later model reranker.

Plans are query hints, never evidence or identity assertions. Output size is
linear in full selected text and the explicit inventory of omitted source IDs.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .benchmark import STOP
from .db import dump, ident, sha
from .reference_ranking import _LexicalRows
from .retrieval import API

_EXPLICIT = {"ATTACHMENT_OF", "EMAIL_REPLY_REFERENCE", "EXPLICIT_DOCUMENT_REFERENCE"}


def _tokens(text):
    return [t.casefold() for t in re.findall(r"[^\W_]+", text)]


def _plan(value):
    if type(value) is not dict or set(value) - {"queries", "phrases", "facets"}:
        raise ValueError("Plan must contain only queries, phrases and facets")
    result = {}
    for key, count, length in (("queries", 16, 200), ("phrases", 12, 200), ("facets", 8, 300)):
        values = value.get(key, [])
        if type(values) is not list or len(values) > count:
            raise ValueError(f"Invalid {key} list (maximum {count})")
        if any(type(v) is not str or not v.strip() or len(v) > length or not _tokens(v) for v in values):
            raise ValueError(f"Invalid {key} text (1..{length} characters)")
        result[key] = list(values)
    return result


class CandidateCollector:
    """Snapshot-bound collector; supplied Store must already exist.

    document_limit caps admitted documents. Round-robin admission bounds each
    document to ceil(max_candidates / admitted_documents) units until smaller
    documents exhaust. Scopes of at most ten documents are all-or-reject: no
    partial packet is passed off as an exhaustive benchmark scope.
    """

    def __init__(self, store, snapshot_id):
        self.store = store
        self.snapshot_id = store.snapshot(snapshot_id)["id"]
        self.api = API(store)
        self.lexical = _LexicalRows(store)
        self.documents = {}
        for row in store.rows(
            """SELECT sd.document_version_id,se.relative_path FROM snapshot_documents sd
            JOIN document_versions d ON d.id=sd.document_version_id
            JOIN source_entries se ON se.id=d.source_entry_id WHERE sd.snapshot_id=?
            ORDER BY se.relative_path,sd.document_version_id""",
            (self.snapshot_id,),
        ):
            self.documents[row["document_version_id"]] = row["relative_path"]
        self.units = {}
        self.by_doc = defaultdict(list)
        for row in store.rows(
            """SELECT s.id,s.ordinal,s.text,s.text_sha,e.document_version_id
            FROM snapshot_documents sd JOIN extractions e ON e.id=sd.extraction_id
            JOIN segments s ON s.extraction_id=e.id WHERE sd.snapshot_id=?
            ORDER BY s.ordinal,s.id""",
            (self.snapshot_id,),
        ):
            row["nonempty"] = bool(row.pop("text").strip())
            self.units[row["id"]] = row
            self.by_doc[row["document_version_id"]].append(row["id"])

    @property
    def execution_identity(self):
        import inspect

        return {
            "policy": "accuracy-candidates-v1",
            "snapshot_id": self.snapshot_id,
            "manifest_sha": self.store.snapshot(self.snapshot_id)["manifest_sha"],
            "implementation_sha": sha(Path(__file__).read_bytes()),
            "lexical_adapter_sha": sha(inspect.getsource(_LexicalRows)),
            "retrieval_sha": sha(inspect.getsource(API)),
            "stop_terms_sha": sha(dump(sorted(STOP))),
        }

    def collect(self, question, plan, max_candidates=80, document_limit=16, scope_document_ids=None):
        if type(question) is not str or not question.strip() or len(question) > 4096:
            raise ValueError("Question must contain 1..4096 characters")
        plan = _plan(plan)
        for name, value in (("max_candidates", max_candidates), ("document_limit", document_limit)):
            if type(value) is not int or not 1 <= value <= 10000:
                raise ValueError(f"{name} must be an integer in 1..10000")
        self.store.snapshot(self.snapshot_id)
        scope = set(self.documents)
        if scope_document_ids is not None:
            if (
                type(scope_document_ids) not in (list, tuple)
                or not scope_document_ids
                or any(type(d) is not str for d in scope_document_ids)
                or len(set(scope_document_ids)) != len(scope_document_ids)
            ):
                raise ValueError("Scope must be a nonempty unique document ID list")
            scope = set(scope_document_ids)
            if not scope <= self.documents.keys():
                raise ValueError("Scope contains foreign snapshot document IDs")
        exhaustive = scope_document_ids is not None and len(scope) <= 10
        universe = {s for d in scope for s in self.by_doc[d]}
        reasons, scores = defaultdict(list), defaultdict(float)
        per_probe = []
        eligible = {s for s in universe if self.units[s]["nonempty"]}
        if exhaustive:
            candidates = set(eligible)
            for sid in sorted(candidates):
                reasons[sid].append({"type": "complete_explicit_scope"})
        else:
            candidates, strong = set(), set()
            probes = [("question", 0, question)] + [
                (key, i, text) for key, values in plan.items() for i, text in enumerate(values)
            ]
            cache = {}

            def hits(text):
                if text not in cache:
                    cache[text] = [
                        h for h in self.lexical.search(self.snapshot_id, text)["items"] if h["id"] in eligible
                    ]
                return cache[text]

            question_tokens = " " + " ".join(_tokens(question)) + " "
            for origin, index, text in probes:
                terms = sorted(set(_tokens(text)) - STOP)
                if not terms:
                    terms = sorted(set(_tokens(text)))
                phrase = " ".join(_tokens(text))
                searches = [(t, False) for t in terms]
                if len(_tokens(text)) > 1:
                    searches.append((phrase, True))
                weights, matched, phrase_sids = {}, defaultdict(set), set()
                for query, is_phrase in searches:
                    rows = hits(query)
                    degree = len({h["document_version_id"] for h in rows})
                    weight = 1 + math.log((len(scope) + 1) / (degree + 1))
                    backed = is_phrase and (" " + phrase + " ") in question_tokens
                    multiplier = 3 if backed else 1
                    if not is_phrase:
                        weights[query] = weight
                    per_probe.append(
                        {
                            "origin": origin,
                            "index": index,
                            "query": query,
                            "phrase": is_phrase,
                            "matches": len(rows),
                        }
                    )
                    for rank, hit in enumerate(rows, 1):
                        sid = hit["id"]
                        candidates.add(sid)
                        scores[sid] += multiplier * weight / (60 + rank)
                        if is_phrase:
                            phrase_sids.add(sid)
                        else:
                            matched[sid].add(query)
                for sid in set(matched) | phrase_sids:
                    coverage = sum(weights[t] for t in sorted(matched[sid])) / (sum(weights.values()) or 1)
                    scores[sid] += coverage
                    reasons[sid].append(
                        {
                            "type": "query_hint",
                            "origin": origin,
                            "index": index,
                            "coverage": coverage,
                            "exact_phrase": sid in phrase_sids,
                            "terms": sorted(matched[sid]),
                        }
                    )
                    if coverage >= 0.5 or sid in phrase_sids:
                        strong.add(self.units[sid]["document_version_id"])
            # Entire original sections are eligible, including later attachment
            # sections. Expansion never recursively makes new graph seeds.
            for doc in sorted(strong):
                for sid in self.by_doc[doc]:
                    if sid in eligible:
                        candidates.add(sid)
                        reasons[sid].append({"type": "strong_document_sections", "document_version_id": doc})
            for link in self.store.rows(
                """SELECT l.* FROM snapshot_links sl JOIN explicit_links l ON l.id=sl.link_id
                WHERE sl.snapshot_id=? ORDER BY l.id""",
                (self.snapshot_id,),
            ):
                left, right = link["from_node"], link["to_node"]
                if (
                    link["status"] != "resolved"
                    or link["relation_type"] not in _EXPLICIT
                    or left not in scope
                    or right not in scope
                ):
                    continue
                for seed, target in ((left, right), (right, left)):
                    if seed not in strong or seed == target:
                        continue
                    for sid in self.by_doc[target]:
                        if sid in eligible:
                            candidates.add(sid)
                            reasons[sid].append(
                                {
                                    "type": "resolved_endpoint",
                                    "link_id": link["id"],
                                    "relationship": link["relation_type"],
                                    "seed_document": seed,
                                }
                            )
        doc_scores = defaultdict(float)
        grouped = defaultdict(list)
        for sid in candidates:
            doc = self.units[sid]["document_version_id"]
            grouped[doc].append(sid)
            doc_scores[doc] = max(doc_scores[doc], scores[sid])
        # Direct endpoints inherit a bounded admission priority from an original
        # lexical seed. Without this, zero-lexical attachment pages were eligible
        # but excluded by the document cap before the model could inspect them.
        lexical_doc_scores = dict(doc_scores)
        for sid in candidates:
            doc = self.units[sid]["document_version_id"]
            for reason in reasons[sid]:
                if reason["type"] == "resolved_endpoint":
                    inherited = 0.85 * lexical_doc_scores.get(reason["seed_document"], 0)
                    doc_scores[doc] = max(doc_scores[doc], inherited)
        doc_order = sorted(grouped, key=lambda d: (-doc_scores[d], self.documents[d], d))
        rejected = exhaustive and (len(candidates) > max_candidates or len(scope) > document_limit)
        admitted = [] if rejected else doc_order[:document_limit]
        for doc in admitted:
            grouped[doc].sort(key=lambda s: (-scores[s], self.units[s]["ordinal"], s))
        selected = []
        for position in range(max((len(grouped[d]) for d in admitted), default=0)):
            for doc in admitted:
                if position < len(grouped[doc]) and len(selected) < max_candidates:
                    selected.append(grouped[doc][position])
            if len(selected) >= max_candidates:
                break
        chosen = set(selected)
        remaining = {
            "scope_over_limit": [],
            "document_limit": [],
            "candidate_limit": [],
            "not_candidate": [],
            "empty_text": [],
        }
        for sid in sorted(universe - chosen):
            doc = self.units[sid]["document_version_id"]
            reason = (
                "empty_text"
                if sid not in eligible
                else "scope_over_limit"
                if rejected
                else "not_candidate"
                if sid not in candidates
                else "document_limit"
                if doc not in admitted
                else "candidate_limit"
            )
            remaining[reason].append(sid)
        originals, metadata = [], {}
        for sid in selected:
            original = self.api.segment(self.snapshot_id, sid)
            extraction = self.store.one(
                "SELECT artifact_sha FROM extractions WHERE id=?", (original["extraction_id"],)
            )
            artifact = json.loads(self.store.get(extraction["artifact_sha"]))
            if artifact["text"][original["char_start"] : original["char_end"]] != original["text"]:
                raise ValueError("Segment differs from immutable extraction artifact")
            expected_id = ident(
                "S",
                original["extraction_id"],
                original["ordinal"],
                original["char_start"],
                original["char_end"],
            )
            expected_locations = [
                loc
                for loc in artifact["locators"]
                if loc["end"] >= original["char_start"] and loc["start"] <= original["char_end"]
            ]
            if original["id"] != expected_id or original["locators"] != expected_locations:
                raise ValueError("Segment identity or locator provenance drift")
            locators = original["locators"]
            preview = []
            for locator in locators[:8]:
                if len(dump(preview + [locator]).encode()) > 1024:
                    break
                preview.append(locator)
            if len(preview) < len(locators):
                original = {
                    **original,
                    "locators": preview,
                    "locators_remaining": len(locators) - len(preview),
                    "locators_continuation": {
                        "method": "segment_locators",
                        "snapshot_id": self.snapshot_id,
                        "segment_id": sid,
                        "limit": 100,
                    },
                }
            originals.append(original)
            metadata[sid] = {
                "source_path": self.documents[original["document_version_id"]],
                "document_version_id": original["document_version_id"],
            }
        return {
            "snapshot_id": self.snapshot_id,
            "policy": "accuracy-candidates-v1",
            "segments": originals,
            "metadata": metadata,
            "reasons": {s: deepcopy(reasons[s]) for s in selected},
            "scores": {s: scores[s] for s in selected},
            "plan": plan,
            "plan_sha": sha(dump(plan)),
            "plan_attribution": "caller-supplied query-only hints; not source evidence",
            "scope_document_ids": sorted(scope),
            "exhaustive_scope": exhaustive,
            "rejected": rejected,
            "rejection_reason": "scope_exceeds_limits" if rejected else None,
            "selected_count": len(selected),
            "candidate_counts": {
                "union": len(candidates),
                "scope_units": len(universe),
                "scope_nonempty": len(eligible),
                "probes": per_probe,
            },
            "remaining": remaining,
            "omitted_count": len(universe) - len(selected),
            "max_candidates": max_candidates,
            "document_limit": document_limit,
        }
