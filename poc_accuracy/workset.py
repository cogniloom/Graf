"""Snapshot-scoped durable candidate inventory; ranking never deletes eligibility."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from evidencekg.db import dump, sha
from evidencekg.experiments import _safe


class Workset:
    def __init__(self, path):
        path = _safe(path)
        for suffix in ("-wal", "-shm", "-journal"):
            _safe(str(path) + suffix)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, identity TEXT NOT NULL, manifest TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS candidates(run TEXT NOT NULL, sid TEXT NOT NULL, position INTEGER NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', receipt TEXT,
            PRIMARY KEY(run,sid), UNIQUE(run,position));
        """)

    def freeze(self, identity, rows, accounting):
        if len({r["segment_id"] for r in rows}) != len(rows):
            raise ValueError("Duplicate candidate identity")
        manifest = {"rows_sha": sha(dump(rows)), "accounting": accounting, "count": len(rows)}
        key = sha(dump(identity))
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT * FROM runs WHERE id=?", (key,)).fetchone()
            if existing:
                if existing["identity"] != dump(identity) or existing["manifest"] != dump(manifest):
                    raise ValueError("Frozen workset drift")
                retained = [
                    json.loads(r[0])
                    for r in self.db.execute(
                        "SELECT payload FROM candidates WHERE run=? ORDER BY position", (key,)
                    )
                ]
                if sha(dump(retained)) != manifest["rows_sha"]:
                    raise ValueError("Candidate inventory drift")
            else:
                self.db.execute("INSERT INTO runs VALUES(?,?,?)", (key, dump(identity), dump(manifest)))
                self.db.executemany(
                    "INSERT INTO candidates(run,sid,position,payload) VALUES(?,?,?,?)",
                    [(key, r["segment_id"], i, dump(r)) for i, r in enumerate(rows)],
                )
        return key

    def page(self, key, *, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid page size")
        run = self.db.execute("SELECT * FROM runs WHERE id=?", (key,)).fetchone()
        if run is None:
            raise ValueError("Unknown workset")
        stored = list(
            self.db.execute(
                "SELECT sid,position,payload FROM candidates WHERE run=? ORDER BY position", (key,)
            )
        )
        retained = [json.loads(r["payload"]) for r in stored]
        manifest = json.loads(run["manifest"])
        if (
            sha(run["identity"]) != key
            or len(retained) != manifest["count"]
            or sha(dump(retained)) != manifest["rows_sha"]
            or any(r["position"] != i or r["sid"] != retained[i]["segment_id"] for i, r in enumerate(stored))
        ):
            raise ValueError("Candidate inventory drift")
        scope = sha(run["identity"] + run["manifest"])
        start = -1
        if cursor:
            try:
                value = json.loads(base64.urlsafe_b64decode(cursor))
                if value["scope"] != scope or type(value["position"]) is not int or value["position"] < -1:
                    raise ValueError("Foreign cursor")
                start = value["position"]
            except Exception as exc:
                raise ValueError("Invalid or foreign cursor") from exc
        rows = list(
            self.db.execute(
                "SELECT * FROM candidates WHERE run=? AND position>? ORDER BY position LIMIT ?",
                (key, start, limit + 1),
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        nxt = (
            base64.urlsafe_b64encode(
                dump({"scope": scope, "position": rows[-1]["position"]}).encode()
            ).decode()
            if more
            else None
        )
        return {
            "items": [json.loads(r["payload"]) | {"review_state": r["state"]} for r in rows],
            "next_cursor": nxt,
            "total": json.loads(run["manifest"])["count"],
            "accounting": json.loads(run["manifest"])["accounting"],
        }

    def mark_reviewed(self, key, sid, receipt):
        if not isinstance(receipt, dict) or not receipt:
            raise ValueError("Explicit review receipt required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT state,receipt FROM candidates WHERE run=? AND sid=?", (key, sid)
            ).fetchone()
            if row is None:
                raise ValueError("Candidate outside workset")
            if row["state"] == "reviewed" and row["receipt"] != dump(receipt):
                raise ValueError("Review receipt drift")
            self.db.execute(
                "UPDATE candidates SET state='reviewed',receipt=? WHERE run=? AND sid=?",
                (dump(receipt), key, sid),
            )

    def close(self):
        self.db.close()
