"""Equivalent OLD benchmark ranking execution, not a new ranking policy.

For an immutable Store snapshot and enhanced_run_id=None only. The linear index
holds postings and document-feature memberships, never document-pair edges.
Native endpoint timeouts/latency must remain separate experiment observations.
"""

from __future__ import annotations

import inspect
import json
import re
from bisect import insort
from collections import Counter, defaultdict

from . import benchmark
from .db import dump, sha
from .relationships import link_sources, postings, reason, verify_posting
from .retrieval import API

_ORIGINAL_RETRIEVE_SHA = "99201ac255da3637e2e370bfa5acc6fb3f8499aff68371e8c2f8c1819e3582d8"


class _LexicalRows(API):
    """Reuse the endpoint's actual FTS SQL, phrase grammar and BM25 computation.

    Unique segment IDs make complete pagination equivalent to this sorted list.
    Keep the endpoint's single-row byte failure; page boundaries affect neither
    the candidate universe nor score accumulation for an immutable snapshot.
    """

    def page(self, snapshot, scope, rows, cursor=None, limit=100, key=lambda x: x["id"]):
        if scope["kind"] != "search" or scope["mode"] != "lexical" or cursor is not None:
            raise ValueError("Reference execution supports complete lexical reads only")
        ordered = sorted(rows, key=key)
        for row in ordered:
            if len(dump(row).encode()) > 750000:
                raise ValueError("One result exceeds response budget; use bounded source/feature reads")
        return {"items": ordered}


class _Reasons:
    """Top eight canonical keys and exact count of already-unique emissions.

    Lexical terms are distinct. Mechanical emissions have unique (seed, item ID)
    keys; a link's repeated source segment is collapsed before calling add.
    Thus no unbounded per-candidate dedup set or probabilistic hash is necessary.
    """

    __slots__ = ("count", "top")

    def __init__(self):
        self.count = 0
        self.top = []

    def add(self, canonical, *, lexical=False):
        self.count += 1
        key = (not lexical, canonical)
        if len(self.top) < 8 or key < self.top[-1]:
            insort(self.top, key)
            if len(self.top) > 8:
                self.top.pop()

    def selected(self):
        result = [json.loads(canonical) for _, canonical in self.top]
        if self.count > 8:
            result.append({"type": "additional_reasons_omitted", "count": self.count - 8})
        return result


class ReferenceRanking:
    """Reusable snapshot-bound execution of benchmark.retrieve's OLD formula.

    Construct with (store, snapshot_id), then retrieve(question, arm, limit=6).
    This deliberately does not accept enhanced observations or alternate APIs.
    In-place mutation of frozen rows/blobs is unsupported: build a fresh instance
    to validate a new snapshot. Returned dictionaries do not alias index storage.
    """

    def __init__(self, store, snapshot_id):
        original_sha = sha(inspect.getsource(benchmark.retrieve).rstrip())
        if original_sha != _ORIGINAL_RETRIEVE_SHA:
            raise ValueError("Original retrieve changed; re-prove reference execution equivalence")
        self.store = store
        self.snapshot_id = store.snapshot(snapshot_id)["id"]
        self._api = API(store)
        self._lexical = _LexicalRows(store)
        self._features = defaultdict(set)
        self._postings = defaultdict(list)
        self._links = defaultdict(list)
        self.verified_postings = 0
        # Preserve the original inner SQL's scope: it intentionally has no join
        # to snapshot_occurrences. Do not derive this membership from postings.
        for row in store.rows(
            """SELECT DISTINCT sd.document_version_id,o.feature_id FROM occurrences o
            JOIN segments s ON s.id=o.segment_id
            JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id
            WHERE sd.snapshot_id=?""",
            (self.snapshot_id,),
        ):
            self._features[row["document_version_id"]].add(row["feature_id"])
        rows = postings(store, self.snapshot_id)
        # Verification cache lifetime is one extraction, avoiding retention of
        # every decoded original in addition to the linear membership index.
        cache, extraction = {}, None
        for row in sorted(rows, key=lambda r: (r["extraction_id"], r["id"])):
            if row["extraction_id"] != extraction:
                cache.clear()
                extraction = row["extraction_id"]
            verify_posting(store, row, cache)
            self.verified_postings += 1
            item = dict(id=row["id"], **reason(row))
            self._postings[row["feature_id"]].append(
                (
                    row["document_version_id"],
                    row["segment_id"],
                    self._encode(item),
                    item["relationship"] not in {"SHARED_NAME_SURFACE", "SHARED_DATE"},
                )
            )
        for link in store.rows(
            """SELECT l.* FROM snapshot_links sl JOIN explicit_links l ON l.id=sl.link_id
            WHERE sl.snapshot_id=?""",
            (self.snapshot_id,),
        ):
            item = dict(
                id=link["id"],
                relationship=link["relation_type"],
                from_node=link["from_node"],
                to_node=link["to_node"],
                status=link["status"],
                derivation=json.loads(link["derivation_json"]),
                rule_version=link["rule_version"],
                sources=link_sources(store, self.snapshot_id, link["id"]),
            )
            # Full original sources remain in detail; dedup only emission to a
            # candidate (multiple spans in one segment, and self-loop endpoints).
            value = (
                tuple(sorted({r["segment_id"] for r in item["sources"]})),
                self._encode(item),
                item["relationship"] not in {"SHARED_NAME_SURFACE", "SHARED_DATE"},
            )
            for endpoint in {link["from_node"], link["to_node"]}:
                self._links[endpoint].append(value)
        self.execution_identity = {
            "kind": "equivalent-old-ranking-execution-v1",
            "snapshot_id": self.snapshot_id,
            "manifest_sha": store.snapshot(self.snapshot_id)["manifest_sha"],
            "enhanced_run_id": None,
            "original_retrieve_sha": original_sha,
            "adapter_source_sha": sha(inspect.getsource(inspect.getmodule(type(self)))),
            "verified_postings": self.verified_postings,
            "native_endpoint_latency_preserved_separately": True,
        }

    @staticmethod
    def _encode(item):
        canonical = dump(item)
        # Native neighbours fails if any one row cannot fit its response page.
        # Store the failure as a sentinel: an unreachable oversized row must not
        # make an unrelated query fail, unlike occurrence integrity verification.
        return canonical if len(canonical.encode()) <= 750000 else None

    @staticmethod
    def _mechanical(detail, seed):
        if detail is None:
            raise ValueError("One result exceeds response budget; use bounded source/feature reads")
        # These are exactly dump's sorted wrapper keys, not a hash surrogate.
        return '{"detail":' + detail + ',"seed_document":' + seed + ',"type":"mechanical_neighbour"}'

    def retrieve(self, question, arm, limit=6):
        if arm not in {"baseline", "graph"} or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Unknown arm or invalid limit")
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError("Question must contain 1..4096 characters")
        snapshot_id = self.snapshot_id
        self.store.snapshot(snapshot_id)
        terms = sorted(
            {t.casefold() for t in re.findall(r"[^\W_]+", question) if t.casefold() not in benchmark.STOP}
        )
        candidates, lexical, graph, observations = {}, set(), set(), set()
        term_counts = {}

        def candidate(sid):
            if sid not in candidates:
                candidates[sid] = {
                    "segment": self._api.segment(snapshot_id, sid),
                    "reasons": _Reasons(),
                    "terms": set(),
                    "bm25": 0.0,
                    "tier": 3,
                }
            return candidates[sid]

        for term in terms:
            hits = self._lexical.search(snapshot_id, term, mode="lexical")["items"]
            term_counts[term] = len(hits)
            for hit in hits:
                sid = hit["id"]
                row = candidate(sid)
                row["terms"].add(term)
                row["bm25"] += hit["score"]
                row["tier"] = 0
                row["reasons"].add(
                    dump({"type": "lexical_bm25", "term": term, "score": hit["score"]}), lexical=True
                )
                lexical.add(sid)
        if arm == "graph":
            docs = sorted({candidates[s]["segment"]["document_version_id"] for s in lexical})

            def emit(sid, canonical, strong):
                row = candidate(sid)
                row["tier"] = min(row["tier"], 1 if strong else 3)
                row["reasons"].add(canonical)
                graph.add(sid)

            for doc in docs:
                seed = dump(doc)
                for feature in self._features.get(doc, ()):
                    for target, sid, detail, strong in self._postings.get(feature, ()):
                        if target != doc:
                            emit(sid, self._mechanical(detail, seed), strong)
                for sids, detail, strong in self._links.get(doc, ()):
                    canonical = self._mechanical(detail, seed)
                    for sid in sids:
                        emit(sid, canonical, strong)
        rarity = {sid: sum(1 / term_counts[t] for t in row["terms"]) for sid, row in candidates.items()}
        ranked = sorted(
            candidates,
            key=lambda s: (
                candidates[s]["tier"],
                -rarity[s],
                -len(candidates[s]["terms"]),
                candidates[s]["bm25"],
                s,
            ),
        )
        occurrence = Counter()
        diversity = {}
        for sid in ranked:
            row = candidates[sid]
            key = (row["tier"], row["segment"]["document_version_id"])
            diversity[sid] = occurrence[key]
            occurrence[key] += 1
        ranked.sort(
            key=lambda s: (
                candidates[s]["tier"],
                diversity[s],
                -rarity[s],
                -len(candidates[s]["terms"]),
                candidates[s]["bm25"],
                s,
            )
        )
        if arm == "graph":
            pools = {tier: [sid for sid in ranked if candidates[sid]["tier"] == tier] for tier in range(4)}
            distinctive = []
            # Alternate strong mechanical and attributed pools without treating counts as truth.
            for i in range(max(len(pools[1]), len(pools[2]))):
                for tier in (1, 2):
                    if i < len(pools[tier]):
                        distinctive.append(pools[tier][i])
            fused = []
            for i in range(max((len(pools[0]) + 1) // 2, len(distinctive))):
                fused.extend(pools[0][2 * i : 2 * i + 2])
                fused.extend(distinctive[i : i + 1])
            ranked = fused + pools[3]
        segments, reasons, skipped = [], {}, []
        for sid in ranked:
            row = candidates[sid]
            why = row["reasons"].selected()
            proposed = {"segments": segments + [row["segment"]], "reasons": {**reasons, sid: why}}
            if len(segments) >= limit or len(dump(proposed).encode()) > benchmark.MAX_EVIDENCE_BYTES:
                skipped.append(sid)
                continue
            segments, reasons = proposed["segments"], proposed["reasons"]
        return {
            "snapshot_id": snapshot_id,
            "arm": arm,
            "segments": segments,
            "reasons": reasons,
            "candidate_counts": {
                "lexical": len(lexical),
                "mechanical": len(graph),
                "observations": len(observations),
                "union": len(candidates),
                "per_term": term_counts,
            },
            "ranking_policy": "document-diverse inverse term-frequency/BM25; two lexical then one distinctive; mechanical/attributed rotation; name/date-only last",
            "selected_count": len(segments),
            "omitted_count": len(skipped),
            "limit": limit,
            "max_evidence_bytes": benchmark.MAX_EVIDENCE_BYTES,
            "evidence_bytes": len(dump({"segments": segments, "reasons": reasons}).encode()),
            "scope": "all term-phrase matches plus one-hop lexical-document neighbours; no semantic recall claim",
        }
