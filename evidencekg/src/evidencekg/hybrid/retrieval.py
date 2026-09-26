"""Question-only hybrid discovery with retained candidates and explicit bundles."""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from evidencekg.benchmark import STOP
from evidencekg.db import dump, sha
from evidencekg.reference_ranking import _LexicalRows
from evidencekg.relationships import postings, verify_posting

from .sources import load_sources


@dataclass(frozen=True)
class Policy:
    dense_candidates: int = 100
    rerank_candidates: int = 128
    seed_documents: int = 12
    graph_depth: int = 2
    graph_documents: int = 64
    bundles: int = 24
    bundle_segments: int = 4
    evidence_bytes: int = 45000


class HybridDiscovery:
    def __init__(
        self,
        store,
        snapshot,
        dense_search,
        reranker,
        worksets,
        *,
        model_identity,
        policy=None,
        sources=None,
        typed=None,
        lexical=None,
    ):
        self.store, self.snapshot = store, snapshot
        self.manifest, self.segments, self.links = (
            sources if sources is not None else load_sources(store, snapshot)
        )
        self.by_doc = defaultdict(list)
        for sid, segment in self.segments.items():
            self.by_doc[segment["document_version_id"]].append(sid)
        for ids in self.by_doc.values():
            ids.sort(key=lambda sid: (self.segments[sid]["ordinal"], sid))
        self.adjacency = defaultdict(list)
        for link in self.links:
            if (
                link["status"] == "resolved"
                and link["from_node"] in self.by_doc
                and link["to_node"] in self.by_doc
            ):
                for node in {link["from_node"], link["to_node"]}:
                    self.adjacency[node].append(link)
        self.lexical = lexical if lexical is not None else _LexicalRows(store)
        self.typed = []
        cache = {}
        for posting in typed if typed is not None else postings(store, snapshot):
            if posting["kind"] in {
                "identifier",
                "message_id",
                "reply_reference",
                "document_reference",
                "name",
            }:
                if typed is None:
                    verify_posting(store, posting, cache)
                self.typed.append(posting)
        self.dense_search, self.reranker, self.worksets = dense_search, reranker, worksets
        self.policy = policy or Policy()
        self.identity = {
            "version": "hybrid-bundle-v1",
            "snapshot_id": snapshot,
            "manifest_sha": self.manifest["hybrid_manifest_sha"]
            if sources is not None
            else store.snapshot(snapshot)["manifest_sha"],
            "policy": asdict(self.policy),
            "models": model_identity,
            "code_sha": sha(Path(__file__).read_bytes()),
        }

    def context(self, sid):
        s = self.segments[sid]
        return {
            "segment_id": sid,
            "source_path": s["source_path"],
            "locators": s["locators"],
            "document_version_id": s["document_version_id"],
            "text": s["text"],
        }

    def retrieve(self, question, limit=12):
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError("Bounded nonempty question required")
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("Expected limit1..12")
        begin = time.monotonic()
        routes, reasons, fused = {}, defaultdict(list), defaultdict(float)
        eligible = {sid for sid, s in self.segments.items() if s["text"].strip()}

        def route(name, ids):
            ids = list(dict.fromkeys(ids))
            if any(sid not in eligible for sid in ids):
                raise ValueError("Retrieval returned foreign or empty source")
            routes[name] = ids
            for rank, sid in enumerate(ids, 1):
                fused[sid] += 1 / (60 + rank)
                reasons[sid].append({"route": name, "rank": rank})

        terms = sorted({t.casefold() for t in re.findall(r"[^\W_]+", question) if t.casefold() not in STOP})
        lexical_scores = defaultdict(float)
        for term in terms:
            hits = self.lexical.search(self.snapshot, term)["items"]
            for rank, hit in enumerate(hits, 1):
                if hit["id"] in eligible:
                    lexical_scores[hit["id"]] += 1 / (60 + rank)
        route("lexical", sorted(lexical_scores, key=lambda sid: (-lexical_scores[sid], sid)))
        # Literal routes keep punctuation, case and identifiers, independent of tokenizer folding.
        probes = [question] + re.findall(r'["“]([^"”]+)["”]', question)
        probes += re.findall(r"\b[\w.+-]+@[\w.-]+\b|\b\d[\w./-]*\b", question)
        literal = set()
        for probe in dict.fromkeys(probes):
            for doc, sids in self.by_doc.items():
                text = "".join(self.segments[sid]["text"] for sid in sids)
                start = 0
                while (start := text.find(probe, start)) >= 0:
                    end = start + len(probe)
                    for sid in sids:
                        s = self.segments[sid]
                        if s["char_start"] < end and s["char_end"] > start and sid in eligible:
                            literal.add(sid)
                    start += max(1, len(probe))
        route("literal", sorted(literal))
        identifiers = set()
        for posting in self.typed:
            value = posting["canonical_value"]
            if value and re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", question, re.IGNORECASE):
                sid = posting["segment_id"]
                if sid in eligible:
                    identifiers.add(sid)
                    reasons[sid].append(
                        {
                            "route": "identifier",
                            "feature_id": posting["feature_id"],
                            "occurrence_id": posting["id"],
                            "kind": posting["kind"],
                            "namespace": posting["namespace"],
                        }
                    )
        route("identifier", sorted(identifiers))
        dense = self.dense_search(question)
        route("dense", [sid for sid, score in dense[: self.policy.dense_candidates]])
        base_order = sorted(fused, key=lambda sid: (-fused[sid], sid))
        seed_docs = list(dict.fromkeys(self.segments[sid]["document_version_id"] for sid in base_order))[
            : self.policy.seed_documents
        ]
        seen, frontier = set(seed_docs), list(seed_docs)
        graph, traversed, pending = set(), {}, []
        for depth in range(1, self.policy.graph_depth + 1):
            following = []
            for doc in frontier:
                for link in self.adjacency.get(doc, []):
                    target = link["to_node"] if link["from_node"] == doc else link["from_node"]
                    if target not in seen and len(seen) >= self.policy.graph_documents:
                        pending.append({"link_id": link["id"], "target_document": target, "depth": depth})
                        continue
                    traversed[link["id"]] = link
                    if target not in seen:
                        seen.add(target)
                        following.append(target)
                    for sid in self.by_doc[target]:
                        if sid in eligible:
                            graph.add(sid)
                            reasons[sid].append(
                                {
                                    "route": "explicit_graph",
                                    "link_id": link["id"],
                                    "seed_document": doc,
                                    "depth": depth,
                                }
                            )
            frontier = following
        for doc in frontier:
            for link in self.adjacency.get(doc, []):
                target = link["to_node"] if link["from_node"] == doc else link["from_node"]
                if target not in seen:
                    pending.append(
                        {
                            "link_id": link["id"],
                            "target_document": target,
                            "depth": self.policy.graph_depth + 1,
                        }
                    )
        route("explicit_graph", sorted(graph, key=lambda sid: (-fused.get(sid, 0), sid)))
        structural = set()
        for sid in base_order[:32]:
            ids = self.by_doc[self.segments[sid]["document_version_id"]]
            i = ids.index(sid)
            structural.update(x for x in ids[max(0, i - 1) : i + 2] if x in eligible)
        route("structural", sorted(structural, key=lambda sid: (-fused.get(sid, 0), sid)))
        ordered = sorted(fused, key=lambda sid: (-fused[sid], sid))
        # A bounded cross-encoder preview; the rest remains in the durable workset.
        assessed = ordered[: self.policy.rerank_candidates]
        singles = self.reranker.score(question, [dump(self.context(sid)) for sid in assessed])
        scores = dict(zip(assessed, singles.scores, strict=True))
        ranked = sorted(assessed, key=lambda sid: (-scores[sid], -fused[sid], sid))
        bundles, keys = [], set()
        for sid in ranked[: self.policy.bundles]:
            doc = self.segments[sid]["document_version_id"]
            ids = self.by_doc[doc]
            i = ids.index(sid)
            members = [sid]
            # Explicit endpoints precede neighbours; prefer separately scored original evidence.
            bundle_links = []
            for link in self.adjacency.get(doc, []):
                target = link["to_node"] if link["from_node"] == doc else link["from_node"]
                targets = [x for x in self.by_doc[target] if x in scores]
                if targets:
                    members.append(min(targets, key=lambda x: (-scores[x], x)))
                    bundle_links.append(link["id"])
            members += [x for x in ids[max(0, i - 1) : i + 2] if x in fused]
            members = list(dict.fromkeys(members))[: self.policy.bundle_segments]
            key = tuple(sorted(members))
            if key in keys:
                continue
            keys.add(key)
            retained_docs = {self.segments[x]["document_version_id"] for x in members}
            links = [
                link
                for link in self.links
                if link["id"] in bundle_links
                and link["from_node"] in retained_docs
                and link["to_node"] in retained_docs
            ]
            bundles.append({"segment_ids": members, "links": links})
        bundled = self.reranker.score(
            question,
            [
                dump(
                    {
                        "passages": [self.context(sid) for sid in b["segment_ids"]],
                        "observed_links": b["links"],
                    }
                )
                for b in bundles
            ],
        )
        for b, score in zip(bundles, bundled.scores, strict=True):
            b["score"] = float(score)
        bundles.sort(key=lambda b: (-b["score"], tuple(b["segment_ids"])))
        selected, selected_bundles, omitted_bundles = [], [], []
        for b in bundles:
            proposal = list(dict.fromkeys(selected + b["segment_ids"]))
            if (
                len(proposal) > limit
                or len(dump([self.context(sid) for sid in proposal]).encode()) > self.policy.evidence_bytes
            ):
                omitted_bundles.append(b)
                continue
            if proposal != selected:
                selected = proposal
                selected_bundles.append(b)
        # Singleton candidates fill spare budget; no evidence text is shortened.
        for sid in ranked:
            if sid in selected or len(selected) >= limit:
                continue
            if len(dump([self.context(x) for x in selected + [sid]]).encode()) <= self.policy.evidence_bytes:
                selected.append(sid)
        ordered = ranked + [sid for sid in ordered if sid not in scores]
        rows = [
            {
                "segment_id": sid,
                "routes": reasons[sid],
                "fusion_score": fused[sid],
                "cross_encoder_score": float(scores[sid]) if sid in scores else None,
                "selected": sid in selected,
            }
            for sid in ordered
        ]
        accounting = {
            "scope_segments": len(self.segments),
            "nonempty": len(eligible),
            "route_counts": {k: len(v) for k, v in routes.items()},
            "union": len(rows),
            "not_discovered": sorted(eligible - set(ordered)),
            "empty_or_gap": sorted(set(self.segments) - eligible),
            "unreranked": len(ordered) - len(assessed),
            "pending_expansions": pending,
            "unresolved_links": [link["id"] for link in self.links if link["status"] != "resolved"],
            "dense_cutoff": self.policy.dense_candidates,
            "dense_scope_scored": len(dense),
            "scope": "bounded discovery; not exhaustive semantic review",
        }
        workset = self.worksets.freeze(
            {"retrieval": self.identity, "question": question, "limit": limit}, rows, accounting
        )
        return {
            "segments": [self.segments[sid] for sid in selected],
            "snapshot_id": self.snapshot,
            "source_context": {sid: self.context(sid) for sid in selected},
            "bundles": selected_bundles,
            "omitted_bundles": omitted_bundles,
            "workset_id": workset,
            "candidate_accounting": accounting,
            "timing": {"retrieval_seconds": time.monotonic() - begin},
            "local_inference": {
                "candidate_pairs": len(assessed),
                "bundle_pairs": len(bundles),
                "candidate_windows": sum(map(len, singles.windows)),
                "bundle_windows": sum(map(len, bundled.windows)),
            },
            "costs": {"retrieval_passes": 6, "model_calls": 0, "input_bytes": 0, "output_bytes": 0},
        }
