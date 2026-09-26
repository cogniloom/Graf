"""Query-relevant discovery over immutable originals, without pairwise graph edges.

The OLD reference execution stays frozen. This policy preserves its first six
passages and admits supplemental evidence competitively, with no graph quota.
Observation terms are attributed discovery hints, never replacement source text.
"""

from __future__ import annotations

import inspect
import math
import re
from collections import defaultdict
from copy import deepcopy

from .benchmark import STOP
from .db import dump, sha
from .reference_ranking import ReferenceRanking, _LexicalRows
from .relationships import postings, verify_posting
from .retrieval import API
from .validation import validate_refs

MAX_OUTPUT_BYTES = 60000
POLICY = "ranked-discovery-v2"
EXPLICIT = frozenset({"ATTACHMENT_OF", "EMAIL_REPLY_REFERENCE", "EXPLICIT_DOCUMENT_REFERENCE"})
TYPED = frozenset({"identifier", "message_id", "reply_reference", "document_reference"})


def _terms(text):
    return {t.casefold() for t in re.findall(r"[^\W_]+", text) if t.casefold() not in STOP}


class RankedDiscovery:
    """Reusable snapshot-bound discovery. Construction/retrieval perform no writes.

    observations optionally maps segment IDs to {search_text, terms, provenance}
    dictionaries; attribution is a provenance alias. Optional sources must be
    valid original quotation spans. Neither observations nor graph reasons are
    assertions of truth. Rebuild the instance for a different frozen snapshot.
    """

    def __init__(self, store, snapshot_id, observations=None):
        self.store = store
        self.snapshot_id = store.snapshot(snapshot_id)["id"]
        self.api = API(store)
        self._lexical = _LexicalRows(store)
        self._reference = None
        self._documents = defaultdict(list)
        self._segments = {}
        self._contexts = {}
        self._copies = defaultdict(list)
        rows = store.rows(
            """SELECT s.id,s.ordinal,s.text_sha,s.modality,s.text,e.document_version_id,s.extraction_id
            FROM snapshot_documents sd JOIN extractions e ON e.id=sd.extraction_id
            JOIN segments s ON s.extraction_id=e.id WHERE sd.snapshot_id=?
            ORDER BY e.document_version_id,s.ordinal,s.id""",
            (self.snapshot_id,),
        )
        for row in rows:
            row["has_text"] = bool(row.pop("text").strip())
            self._segments[row["id"]] = row
            self._documents[row["document_version_id"]].append(row["id"])
        for sids in self._documents.values():
            for i, sid in enumerate(sids):
                row = self._segments[sid]
                key = (
                    row["text_sha"],
                    row["modality"],
                    self._segments[sids[i - 1]]["text_sha"] if i else None,
                    self._segments[sids[i + 1]]["text_sha"] if i + 1 < len(sids) else None,
                )
                self._contexts[sid] = key
                self._copies[key].append(sid)
        self._features = defaultdict(set)
        self._postings = defaultdict(list)
        self._feature_docs = defaultdict(set)
        cache, extraction = {}, None
        for row in sorted(postings(store, self.snapshot_id), key=lambda r: (r["extraction_id"], r["id"])):
            if row["segment_id"] not in self._segments:
                raise ValueError("Feature posting outside snapshot document scope")
            if row["extraction_id"] != extraction:
                cache.clear()
                extraction = row["extraction_id"]
            verify_posting(store, row, cache)
            feature, doc = row["feature_id"], row["document_version_id"]
            self._features[doc].add(feature)
            self._postings[feature].append(row)
            self._feature_docs[feature].add(doc)
        self._links = defaultdict(list)
        for link in store.rows(
            """SELECT l.* FROM snapshot_links sl JOIN explicit_links l ON l.id=sl.link_id
            WHERE sl.snapshot_id=? ORDER BY l.id""",
            (self.snapshot_id,),
        ):
            if link["relation_type"] not in EXPLICIT or link["status"] != "resolved":
                continue
            if link["from_node"] not in self._documents or link["to_node"] not in self._documents:
                continue
            for endpoint in {link["from_node"], link["to_node"]}:
                self._links[endpoint].append(link)
        self._observations = {}
        self._observation_terms = defaultdict(set)
        source_paths = (
            {
                row["document_version_id"]: row["relative_path"]
                for row in store.rows(
                    """SELECT sd.document_version_id,se.relative_path FROM snapshot_documents sd
                JOIN document_versions d ON d.id=sd.document_version_id
                JOIN source_entries se ON se.id=d.source_entry_id WHERE sd.snapshot_id=?""",
                    (self.snapshot_id,),
                )
            }
            if observations
            else {}
        )
        for sid, observation in sorted((observations or {}).items()):
            if sid not in self._segments:
                raise ValueError("Observation outside snapshot document scope")
            if not isinstance(observation, dict):
                raise ValueError("Observation must be an attributed dictionary")
            expected = {
                **self._segments[sid],
                "segment_id": sid,
                "snapshot_id": self.snapshot_id,
                "source_text_sha": self._segments[sid]["text_sha"],
                "source_path": source_paths[self._segments[sid]["document_version_id"]],
            }
            for identity in (observation, observation.get("source_identity", {})):
                if not isinstance(identity, dict):
                    raise ValueError("Observation source identity must be a dictionary")
                for key in (
                    "segment_id",
                    "snapshot_id",
                    "document_version_id",
                    "extraction_id",
                    "text_sha",
                    "source_text_sha",
                    "source_path",
                ):
                    if key in identity and identity[key] != expected[key]:
                        raise ValueError("Observation source identity mismatch: " + key)
            provenance = observation.get("provenance") or observation.get("attribution")
            if not provenance:
                raise ValueError("Observation requires attribution/provenance")
            if (
                isinstance(provenance, dict)
                and provenance.get("snapshot_id", self.snapshot_id) != self.snapshot_id
            ):
                raise ValueError("Observation provenance snapshot mismatch")
            text = observation.get("search_text", "")
            lists = [observation.get(key, []) for key in ("terms", "terms_de", "terms_en")]
            if not all(isinstance(values, list) for values in lists):
                raise ValueError("Observation terms must be string lists")
            terms = [term for values in lists for term in values]
            if (
                not isinstance(text, str)
                or not isinstance(terms, list)
                or not all(isinstance(t, str) for t in terms)
            ):
                raise ValueError("Observation search_text/terms must be text/string list")
            if observation.get("sources"):
                refs = observation["sources"]
                if any(r["segment_id"] != sid for r in refs):
                    raise ValueError("Observation sources must refer to its original segment")
                validate_refs(refs, {sid: self.api.segment(self.snapshot_id, sid)})
            value = deepcopy(observation)
            self._observations[sid] = value
            for term in _terms(text + " " + " ".join(terms)):
                self._observation_terms[term].add(sid)
        self.execution_identity = {
            "kind": POLICY,
            "snapshot_id": self.snapshot_id,
            "manifest_sha": store.snapshot(self.snapshot_id)["manifest_sha"],
            "implementation_sha": sha(inspect.getsource(inspect.getmodule(type(self)))),
            "observations_sha": sha(dump(self._observations)),
            "observation_count": len(self._observations),
            "max_output_bytes": MAX_OUTPUT_BYTES,
        }

    def duplicate_aliases(self, segment_id):
        """Complete exact text/context alias references, including the representative.

        Retrieval includes eight references at most with explicit remaining counts;
        this read-only method exposes the complete provenance for that group.
        """
        if segment_id not in self._contexts:
            raise ValueError("Segment outside snapshot document scope")
        return [
            {
                "segment_id": sid,
                "document_version_id": self._segments[sid]["document_version_id"],
                "extraction_id": self._segments[sid]["extraction_id"],
            }
            for sid in sorted(self._copies[self._contexts[segment_id]])
        ]

    def retrieve(self, question, limit=12, baseline=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid limit (expected 1..1000)")
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError("Question must contain 1..4096 characters")
        self.store.snapshot(self.snapshot_id)
        if baseline is None:
            if self._reference is None:
                self._reference = ReferenceRanking(self.store, self.snapshot_id)
            baseline = self._reference.retrieve(question, "baseline", limit=6)
        if not isinstance(baseline, dict):
            raise ValueError("Baseline must be a raw retrieval packet")
        if baseline.get("snapshot_id", self.snapshot_id) != self.snapshot_id:
            raise ValueError("Baseline snapshot mismatch")
        if baseline.get("arm", "baseline") != "baseline":
            raise ValueError("Expected question-only baseline arm")
        baseline_items = baseline.get("segments", baseline.get("items"))
        if not isinstance(baseline_items, list):
            raise ValueError("Baseline requires original segments")
        prefix = baseline_items[: min(6, limit)]
        for segment in prefix:
            if segment.get("id") not in self._segments or segment != self.api.segment(
                self.snapshot_id, segment["id"]
            ):
                raise ValueError("Baseline passage is not an original snapshot segment")
        if len({s["id"] for s in prefix}) != len(prefix):
            raise ValueError("Baseline contains duplicate segment IDs")

        terms = sorted(_terms(question))
        candidates, lexical, mechanical, observed = {}, set(), set(), set()
        term_counts, term_docs = {}, {}

        def candidate(sid):
            if sid not in candidates:
                candidates[sid] = {
                    "terms": set(),
                    "observed_terms": set(),
                    "bm25": 0.0,
                    "score": 0.0,
                    "reasons": {},
                    "reason_count": 0,
                }
            return candidates[sid]

        def explain(sid, why):
            # Each emission is unique: lexical/observation once, otherwise an
            # occurrence or link ID paired with its original seed document.
            row = candidate(sid)
            row["reason_count"] += 1
            row["reasons"][dump(why)] = why
            if len(row["reasons"]) > 4:
                del row["reasons"][max(row["reasons"])]

        for term in terms:
            hits = self._lexical.search(self.snapshot_id, term, mode="lexical")["items"]
            term_counts[term] = len(hits)
            term_docs[term] = {hit["document_version_id"] for hit in hits}
            for hit in hits:
                sid = hit["id"]
                row = candidate(sid)
                row["terms"].add(term)
                row["bm25"] += hit["score"]
                lexical.add(sid)
            for sid in sorted(self._observation_terms.get(term, ())):
                candidate(sid)["observed_terms"].add(term)
                observed.add(sid)

        n_docs = max(1, len(self._documents))
        weights = {t: 1 + math.log((n_docs + 1) / (len(term_docs[t]) + 1)) for t in terms}
        total = sum(weights.values()) or 1.0
        observation_weights = {
            term: 1
            + math.log(
                (n_docs + 1)
                / (
                    1
                    + len(
                        {
                            self._segments[sid]["document_version_id"]
                            for sid in self._observation_terms.get(term, ())
                        }
                    )
                )
            )
            for term in terms
        }
        observation_total = sum(observation_weights.values()) or 1.0
        seeds = {}
        for sid, row in candidates.items():
            relevance = sum(weights[t] for t in sorted(row["terms"])) / total
            row["relevance"] = relevance
            row["score"] = relevance
            if row["terms"]:
                explain(sid, {"type": "lexical_bm25", "terms": sorted(row["terms"]), "score": row["bm25"]})
                doc = self._segments[sid]["document_version_id"]
                distinct = any(len(term_docs[t]) <= max(1, n_docs / 2) for t in row["terms"])
                previous = seeds.get(doc, (0.0, False))
                seeds[doc] = (max(previous[0], relevance), previous[1] or (distinct and relevance >= 0.5))
            if row["observed_terms"]:
                coverage = (
                    sum(observation_weights[t] for t in sorted(row["observed_terms"])) / observation_total
                )
                # Attributed terms complement originals without becoming graph seeds.
                row["score"] = max(row["score"], 0.9 * coverage)
                observation = self._observations[sid]
                explain(
                    sid,
                    {
                        "type": "attributed_observation",
                        "terms": sorted(row["observed_terms"]),
                        "observation_sha": sha(dump(observation)),
                        "provenance": observation.get("provenance") or observation.get("attribution"),
                    },
                )

        # Expand only original lexical seeds. No candidate can seed another hop.
        for doc, (strength, distinctive) in sorted(seeds.items()):
            if strength < 0.5:
                continue
            for link in self._links.get(doc, ()):
                target = link["to_node"] if link["from_node"] == doc else link["from_node"]
                if target == doc:
                    continue
                for sid in self._documents[target]:
                    row = candidate(sid)
                    row["score"] = max(row["score"], 0.95 * strength + 0.25 * row.get("relevance", 0))
                    explain(
                        sid,
                        {
                            "type": "explicit_link",
                            "link_id": link["id"],
                            "relationship": link["relation_type"],
                            "seed_document": doc,
                            "target_document": target,
                            "status": link["status"],
                        },
                    )
                    mechanical.add(sid)
            for feature in sorted(self._features.get(doc, ())):
                members = self._postings[feature]
                info, degree = members[0], len(self._feature_docs[feature])
                if degree < 2:
                    continue
                rare = degree <= max(2, math.ceil(n_docs * 0.1))
                strong = info["kind"] in TYPED and rare and distinctive
                if not strong and not (distinctive and _terms(info["canonical_value"]).intersection(terms)):
                    continue
                for posting in members:
                    sid = posting["segment_id"]
                    if posting["document_version_id"] == doc:
                        continue
                    relevance = candidates.get(sid, {}).get("relevance", 0)
                    if not strong and relevance < 0.5:
                        continue
                    row = candidate(sid)
                    score = strength * (0.65 + 0.35 / degree) if strong else 0.1 * strength / degree
                    row["score"] = max(row["score"], score + (0.25 if strong else 0) * relevance)
                    explain(
                        sid,
                        {
                            "type": "typed_identifier" if strong else "query_relevant_weak_feature",
                            "feature_id": feature,
                            "kind": info["kind"],
                            "namespace": info["namespace"],
                            "document_frequency": degree,
                            "seed_document": doc,
                            "occurrence_id": posting["id"],
                        },
                    )
                    mechanical.add(sid)

        for position, segment in enumerate(prefix, 1):
            explain(segment["id"], {"type": "preserved_baseline", "rank": position})
        ranked = sorted(
            candidates,
            key=lambda sid: (
                -candidates[sid]["score"],
                -len(candidates[sid]["terms"]),
                candidates[sid]["bm25"],
                sid,
            ),
        )
        packet = {
            "snapshot_id": self.snapshot_id,
            "arm": "graph",
            "segments": [],
            "reasons": {},
            "candidate_counts": {
                "lexical": len(lexical),
                "mechanical": len(mechanical),
                "observations": len(observed),
                "union": len(candidates),
                "per_term": term_counts,
            },
            "ranking_policy": POLICY,
            "selected_count": 0,
            "omitted_count": len(candidates),
            "limit": limit,
            "max_evidence_bytes": MAX_OUTPUT_BYTES,
            "evidence_bytes": 0,
            "output_bytes": 0,
            "baseline_preserved_count": 0,
            "duplicate_aliases": {},
            "remaining": {"limit": 0, "byte_budget": 0, "exact_duplicate": 0, "empty_text": 0},
            "scope": "bounded preview of lexical matches, attributed terms and relevance-gated one-hop candidates; no semantic recall claim",
        }

        def measure(value):
            value["selected_count"] = len(value["segments"])
            value["omitted_count"] = len(candidates) - value["selected_count"]
            value["evidence_bytes"] = len(
                dump({"segments": value["segments"], "reasons": value["reasons"]}).encode()
            )
            # A fixed point includes the decimal byte count in its own envelope.
            for _ in range(5):
                size = len(dump(value).encode())
                if size == value["output_bytes"]:
                    break
                value["output_bytes"] = size
            return value["output_bytes"]

        def admit(sid, baseline_rank=None):
            proposed = deepcopy(packet)
            original = self.api.segment(self.snapshot_id, sid)
            # Dense page/paragraph locators can outweigh the entire primary text.
            # Preserve text and IDs; expose bounded metadata with an exact read continuation.
            if len(dump(original.get("locators", [])).encode()) > 1024:
                locators = original["locators"]
                preview = []
                for locator in locators[:8]:
                    if len(dump(preview + [locator]).encode()) > 1024:
                        break
                    preview.append(locator)
                original = {
                    **original,
                    "locators": preview,
                    "locators_remaining": len(locators) - len(preview),
                    "locators_continuation": {
                        "tool": "segment_locators",
                        "snapshot_id": self.snapshot_id,
                        "segment_id": sid,
                        "limit": 100,
                    },
                }
            proposed["segments"].append(original)
            if baseline_rank is not None:
                why = [{"type": "preserved_baseline", "rank": baseline_rank}]
                proposed["baseline_preserved_count"] += 1
            else:
                all_reasons = sorted(candidates[sid]["reasons"].values(), key=dump)
                why = all_reasons[:4]
                if candidates[sid]["reason_count"] > 4:
                    why.append(
                        {"type": "additional_reasons_omitted", "count": candidates[sid]["reason_count"] - 4}
                    )
            proposed["reasons"][sid] = why
            aliases = self.duplicate_aliases(sid)
            if len(aliases) > 1:
                proposed["duplicate_aliases"][sid] = {
                    "items": aliases[:8],
                    "count": len(aliases),
                    "remaining": max(0, len(aliases) - 8),
                    "continuation": {"method": "duplicate_aliases", "segment_id": sid},
                }
            # Reserve enough digits for every remaining counter to grow later.
            reserve = 4 * len(str(len(candidates)))
            if measure(proposed) + reserve > MAX_OUTPUT_BYTES:
                return False
            packet.clear()
            packet.update(proposed)
            return True

        selected, contexts = set(), set()
        for rank, segment in enumerate(prefix, 1):
            if not self._segments[segment["id"]]["has_text"]:
                raise ValueError("Protected baseline has no readable text; inspect inventory gaps")
            if not admit(segment["id"], rank):
                raise ValueError(
                    "Preserved baseline cannot fit 60000-byte output budget; no source text truncated"
                )
            selected.add(segment["id"])
            contexts.add(self._contexts[segment["id"]])
        for sid in ranked:
            if sid in selected:
                continue
            if not self._segments[sid]["has_text"]:
                packet["remaining"]["empty_text"] += 1
            elif self._contexts[sid] in contexts:
                packet["remaining"]["exact_duplicate"] += 1
            elif len(selected) >= limit:
                packet["remaining"]["limit"] += 1
            elif admit(sid):
                selected.add(sid)
                contexts.add(self._contexts[sid])
            else:
                packet["remaining"]["byte_budget"] += 1
        if measure(packet) > MAX_OUTPUT_BYTES:
            raise ValueError("Retrieval metadata exceeds 60000-byte output budget")
        return packet
