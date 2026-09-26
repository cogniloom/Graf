"""Rebuildable in-memory FTS5 preserving the measured Unicode/BM25 route."""

import sqlite3
from functools import lru_cache


class LexicalIndex:
    def __init__(self, segments):
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE VIRTUAL TABLE passages USING fts5(segment_id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2')"
        )
        self.db.executemany(
            "INSERT INTO passages VALUES(?,?)", [(sid, segments[sid]["text"]) for sid in sorted(segments)]
        )

    @lru_cache(maxsize=4096)
    def _search(self, query):
        expression = '"' + query.replace('"', '""') + '"'
        return tuple(
            row["id"]
            for row in self.db.execute(
                "SELECT segment_id AS id,bm25(passages) AS score FROM passages WHERE passages MATCH ? ORDER BY score,id",
                (expression,),
            )
        )

    def search(self, snapshot, query):
        return {"items": [{"id": sid} for sid in self._search(query)]}

    def close(self):
        self._search.cache_clear()
        self.db.close()
