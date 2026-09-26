"""Lossless per-query pagination cache for frozen reference ranking experiments.

Keeps original neighbour query/verification/ranking semantics. Pages are sliced
from one verified, immutable materialization using the original cursor and byte
budget rules. Native un-cached endpoint latency must be reported separately.
"""

import base64
import copy
import json
from bisect import bisect_right

from .db import dump, sha
from .retrieval import API


class ReferencePageCache(API):
    def __init__(self, store):
        super().__init__(store)
        self._request = None
        self._cached = None
        self.materializations = 0
        self.cache_hits = 0

    def page(self, snapshot, scope, rows, cursor=None, limit=100, key=lambda x: x["id"]):
        if self._request is None:
            return super().page(snapshot, scope, rows, cursor, limit, key)
        ordered = sorted(rows, key=key)
        manifest = self.store.manifest(snapshot)
        self._cached = {
            "request": self._request,
            "snapshot": snapshot,
            "scope": scope,
            "signature": sha(dump(scope)),
            "rows": ordered,
            "keys": [key(r) for r in ordered],
            "warnings": sum(bool(d["warnings"]) for d in manifest["documents"]),
            "inventory_complete": manifest["inventory_complete"],
        }
        self.materializations += 1
        return self._page(cursor, limit)

    def _page(self, cursor, limit):
        c = self._cached
        self.store.snapshot(c["snapshot"])
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        start = 0
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor))
                if value["snapshot"] != c["snapshot"] or value["scope"] != c["signature"]:
                    raise ValueError("Cursor scope/snapshot mismatch")
                start = bisect_right(c["keys"], value["after"])
            except Exception as exc:
                raise ValueError("Invalid cursor") from exc
        items = []
        used = 0
        for row in c["rows"][start : start + limit]:
            size = len(dump(row).encode())
            if used + size > 750000:
                if not items:
                    raise ValueError("One result exceeds response budget; use bounded source/feature reads")
                break
            items.append(row)
            used += size
        remaining = len(c["rows"]) - start - len(items)
        cursor = (
            base64.urlsafe_b64encode(
                dump(
                    {
                        "snapshot": c["snapshot"],
                        "scope": c["signature"],
                        "after": c["keys"][start + len(items) - 1],
                    }
                ).encode()
            ).decode()
            if remaining
            else None
        )
        return dict(
            snapshot_id=c["snapshot"],
            items=copy.deepcopy(items),
            next_cursor=cursor,
            total=len(c["rows"]),
            known_matches=len(c["rows"]),
            remaining=remaining,
            extraction_warnings={
                "count": c["warnings"],
                "details": "inventory tool enumerates all document warnings without truncation",
            },
            scope=c["scope"],
            inventory_complete=c["inventory_complete"],
        )

    def neighbours(self, snapshot_id, node_id, relation_types=None, cursor=None, limit=100):
        request = dump([snapshot_id, node_id, relation_types])
        if self._cached is not None and self._cached["request"] == request:
            self.cache_hits += 1
            return self._page(cursor, limit)
        self._cached = None
        self._request = request
        try:
            return super().neighbours(snapshot_id, node_id, relation_types, cursor, limit)
        finally:
            self._request = None
