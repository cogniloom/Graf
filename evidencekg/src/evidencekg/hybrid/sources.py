"""Read-only access to the existing immutable evidence vault."""

import shutil
import sqlite3
import tempfile
from pathlib import Path

from evidencekg.db import Store
from evidencekg.discovery_index import _sources


class ReadOnlyStore(Store):
    def __init__(self, state):
        self.state = Path(state).resolve(strict=True)
        # SQLite mode=ro alone may create/change WAL shared-memory sidecars.
        # Copy a stable main/WAL pair without opening the original in SQLite.
        self._copy = tempfile.TemporaryDirectory(prefix="evidencekg-poc-read-")
        target = Path(self._copy.name) / "evidence.sqlite3"
        paths = [self.state / "evidence.sqlite3", self.state / "evidence.sqlite3-wal"]

        def signatures():
            result = []
            for p in paths:
                if p.exists():
                    s = p.stat()
                    result.append((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns))
                else:
                    result.append(None)
            return result

        before = signatures()
        for p, sig in zip(paths, before, strict=True):
            if sig is not None:
                shutil.copyfile(p, Path(self._copy.name) / p.name)
        if before != signatures():
            self._copy.cleanup()
            raise ValueError("Source database changed during read snapshot copy")
        # Recover/checkpoint only the disposable copy; objects remain hash-verified read access.
        copy = sqlite3.connect(target)
        copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        copy.execute("PRAGMA journal_mode=DELETE")
        copy.close()
        self.db = sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")
        # A fixed read transaction keeps SQL rows coherent with the immutable manifest.
        self.db.execute("BEGIN")

    def close(self):
        self.db.close()
        self._copy.cleanup()

    def write(self, *args, **kwargs):
        raise ValueError("Read-only source store")

    def put(self, *args, **kwargs):
        raise ValueError("Read-only source store")

    def audit(self, *args, **kwargs):
        raise ValueError("Read-only source store")


def load_sources(store, snapshot):
    verified = _sources(store, snapshot)
    segments = {s["id"]: s for s in verified["segments"]}
    links = store.rows(
        """SELECT l.* FROM explicit_links l JOIN snapshot_links sl ON sl.link_id=l.id
                          WHERE sl.snapshot_id=? ORDER BY l.id""",
        (snapshot,),
    )
    return verified["manifest"], segments, links
