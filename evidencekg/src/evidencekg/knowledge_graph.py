"""Private, disposable graph construction indexes with bounded SQLite caches."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

from .db import dump


class Nodes(MutableMapping):
    def __init__(self, db):
        self.db = db

    def __getitem__(self, key):
        row = self.db.execute("SELECT body FROM nodes WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def __setitem__(self, key, value):
        self.db.execute(
            "INSERT INTO nodes(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (key, dump(value)),
        )

    def __delitem__(self, key):
        if not self.db.execute("DELETE FROM nodes WHERE id=?", (key,)).rowcount:
            raise KeyError(key)

    def __iter__(self):
        return (row[0] for row in self.db.execute("SELECT id FROM nodes ORDER BY rowid"))

    def __len__(self):
        return self.db.execute("SELECT count(*) FROM nodes").fetchone()[0]

    def values(self):
        return (json.loads(row[0]) for row in self.db.execute("SELECT body FROM nodes ORDER BY rowid"))


class Edges:
    def __init__(self, db):
        self.db = db

    def append(self, value):
        self.db.execute("INSERT INTO edges(id,body) VALUES(?,?)", (value["id"], dump(value)))

    def extend(self, values):
        for value in values:
            self.append(value)


class Rows:
    def __init__(self, db, table):
        self.db, self.table = db, table

    def __iter__(self):
        # Fixed table names are internal; ties retain append order like sorted().
        return (
            json.loads(row[0]) for row in self.db.execute(f"SELECT body FROM {self.table} ORDER BY id,rowid")
        )


class Graph:
    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="graf-knowledge-")
        try:
            self.db = sqlite3.connect(Path(self.directory.name) / "graph.sqlite3")
            self.db.execute("PRAGMA cache_size=-8192")
            self.db.execute("PRAGMA temp_store=FILE")
            self.db.execute("PRAGMA journal_mode=OFF")
            self.db.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self.db.execute("CREATE TABLE edges(id TEXT NOT NULL, body TEXT NOT NULL)")
            self.db.execute("CREATE INDEX edge_order ON edges(id)")
            self.nodes, self.edges = Nodes(self.db), Edges(self.db)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def rows(self, table):
        return Rows(self.db, table)

    def __exit__(self, *exc):
        if hasattr(self, "db"):
            self.db.close()
        self.directory.cleanup()
