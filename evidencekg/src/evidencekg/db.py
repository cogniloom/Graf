from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def ident(prefix, *parts):
    return prefix + sha(dump(parts))


def atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


SCHEMA = """
CREATE TABLE IF NOT EXISTS corpora(id TEXT PRIMARY KEY,root TEXT NOT NULL,configuration_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_entries(id TEXT PRIMARY KEY,corpus_id TEXT NOT NULL REFERENCES corpora(id),relative_path TEXT NOT NULL,first_seen_at REAL NOT NULL,identity_key TEXT NOT NULL,UNIQUE(corpus_id,identity_key));
CREATE TABLE IF NOT EXISTS blobs(sha256 TEXT PRIMARY KEY,byte_length INTEGER NOT NULL,storage_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS document_versions(id TEXT PRIMARY KEY,source_entry_id TEXT NOT NULL REFERENCES source_entries(id),original_blob_sha TEXT REFERENCES blobs(sha256),observed_at REAL NOT NULL,parent_document_version_id TEXT REFERENCES document_versions(id),mime_part_path TEXT,mime_type TEXT,previous_version_id TEXT REFERENCES document_versions(id));
CREATE INDEX IF NOT EXISTS versions_entry ON document_versions(source_entry_id,observed_at);
CREATE TABLE IF NOT EXISTS extractions(id TEXT PRIMARY KEY,document_version_id TEXT NOT NULL REFERENCES document_versions(id),parser_name TEXT NOT NULL,parser_version TEXT NOT NULL,config_hash TEXT NOT NULL,artifact_sha TEXT NOT NULL REFERENCES blobs(sha256),status TEXT NOT NULL,warnings_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS segments(id TEXT PRIMARY KEY,extraction_id TEXT NOT NULL REFERENCES extractions(id),ordinal INTEGER NOT NULL,text_sha TEXT NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL,locator_json TEXT NOT NULL,modality TEXT NOT NULL,status TEXT NOT NULL,text TEXT NOT NULL,UNIQUE(extraction_id,ordinal));
CREATE INDEX IF NOT EXISTS segments_extraction ON segments(extraction_id,ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS segment_fts USING fts5(segment_id UNINDEXED,text,tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS features(id TEXT PRIMARY KEY,kind TEXT NOT NULL,canonical_value TEXT NOT NULL,namespace TEXT NOT NULL,normalization_version TEXT NOT NULL,UNIQUE(kind,namespace,canonical_value,normalization_version));
CREATE INDEX IF NOT EXISTS feature_key ON features(kind,namespace,canonical_value);
CREATE TABLE IF NOT EXISTS occurrences(id TEXT PRIMARY KEY,feature_id TEXT NOT NULL REFERENCES features(id),segment_id TEXT NOT NULL REFERENCES segments(id),start INTEGER NOT NULL,end INTEGER NOT NULL,raw_value TEXT NOT NULL,rule_id TEXT NOT NULL,rule_version TEXT NOT NULL,ambiguity_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS occurrence_feature ON occurrences(feature_id,segment_id);
CREATE INDEX IF NOT EXISTS occurrence_segment ON occurrences(segment_id,feature_id);
CREATE TABLE IF NOT EXISTS explicit_links(id TEXT PRIMARY KEY,from_node TEXT NOT NULL,relation_type TEXT NOT NULL,to_node TEXT,status TEXT NOT NULL,derivation_json TEXT NOT NULL,rule_id TEXT NOT NULL,rule_version TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS link_from ON explicit_links(from_node);
CREATE INDEX IF NOT EXISTS link_to ON explicit_links(to_node);
CREATE TABLE IF NOT EXISTS link_evidence(link_id TEXT NOT NULL REFERENCES explicit_links(id),segment_id TEXT NOT NULL REFERENCES segments(id),start INTEGER NOT NULL,end INTEGER NOT NULL,PRIMARY KEY(link_id,segment_id,start,end));
CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,manifest_sha TEXT NOT NULL REFERENCES blobs(sha256),ruleset_hash TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS current_snapshot(singleton INTEGER PRIMARY KEY CHECK(singleton=1),snapshot_id TEXT NOT NULL REFERENCES snapshots(id));
CREATE TABLE IF NOT EXISTS snapshot_documents(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),document_version_id TEXT NOT NULL REFERENCES document_versions(id),extraction_id TEXT NOT NULL REFERENCES extractions(id),PRIMARY KEY(snapshot_id,document_version_id));
CREATE TABLE IF NOT EXISTS snapshot_history(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),document_version_id TEXT NOT NULL REFERENCES document_versions(id),extraction_id TEXT NOT NULL REFERENCES extractions(id),PRIMARY KEY(snapshot_id,document_version_id));
CREATE TABLE IF NOT EXISTS snapshot_occurrences(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),occurrence_id TEXT NOT NULL REFERENCES occurrences(id),PRIMARY KEY(snapshot_id,occurrence_id));
CREATE TABLE IF NOT EXISTS snapshot_links(snapshot_id TEXT NOT NULL REFERENCES snapshots(id),link_id TEXT NOT NULL REFERENCES explicit_links(id),PRIMARY KEY(snapshot_id,link_id));
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES snapshots(id),question TEXT NOT NULL,question_sha TEXT NOT NULL,runner_config_json TEXT NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS review_tasks(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),pass_id TEXT NOT NULL,kind TEXT NOT NULL,input_manifest_sha TEXT NOT NULL REFERENCES blobs(sha256),state TEXT NOT NULL,lease_until REAL,lease_id TEXT,worker_id TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,result_sha TEXT REFERENCES blobs(sha256));
CREATE INDEX IF NOT EXISTS task_state ON review_tasks(run_id,state,id);
CREATE TABLE IF NOT EXISTS task_attempts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES review_tasks(id),worker_model TEXT NOT NULL,input_sha TEXT NOT NULL REFERENCES blobs(sha256),output_sha TEXT REFERENCES blobs(sha256),validation_status TEXT NOT NULL,started_at REAL NOT NULL,finished_at REAL,error_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),task_id TEXT NOT NULL REFERENCES review_tasks(id),assertion TEXT NOT NULL,epistemic_status TEXT NOT NULL,payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS finding_sources(finding_id TEXT NOT NULL REFERENCES findings(id),segment_id TEXT NOT NULL REFERENCES segments(id),start INTEGER NOT NULL,end INTEGER NOT NULL,PRIMARY KEY(finding_id,segment_id,start,end));
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),relation_type TEXT NOT NULL,subject_json TEXT NOT NULL,object_json TEXT NOT NULL,status TEXT NOT NULL,explanation TEXT NOT NULL,dependencies_json TEXT NOT NULL,attribution TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS followups(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),issue TEXT NOT NULL,state TEXT NOT NULL,origin_finding_id TEXT REFERENCES findings(id),round INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT REFERENCES runs(id),event_type TEXT NOT NULL,payload_json TEXT NOT NULL,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_run_event ON audit_events(run_id,event_type);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'append only audit'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'append only audit'); END;
"""


class Store:
    def __init__(self, state):
        self.state = Path(state).resolve()
        mounts = Path("/proc/mounts")
        if mounts.exists():
            candidates = []
            for line in mounts.read_text().splitlines():
                fields = line.split()
                if len(fields) > 2:
                    mount = fields[1].replace("\\040", " ")
                    if self.state.is_relative_to(mount):
                        candidates.append((len(mount), fields[2]))
            if candidates and max(candidates)[1] in {"nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs"}:
                raise ValueError("WAL state requires a local filesystem")
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.state / "evidence.sqlite3", timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        if self.db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            raise RuntimeError("WAL unavailable; use local storage")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.write():
            self.db.executescript(SCHEMA)
        self.db.execute("SELECT count(*) FROM segment_fts").fetchone()

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def write(self):
        with (self.state / "writer.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def put(self, data):
        if isinstance(data, str):
            data = data.encode()
        key = sha(data)
        rel = f"objects/{key[:2]}/{key}"
        path = self.state / rel
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("Content-address collision or corrupt artifact")
        else:
            atomic(path, data)
        self.db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?,?)", (key, len(data), rel))
        return key

    def get(self, key):
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid artifact key")
        data = (self.state / "objects" / key[:2] / key).read_bytes()
        if sha(data) != key:
            raise ValueError("Corrupt artifact: " + key)
        return data

    def rows(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        if row is None:
            raise ValueError("Unknown or out-of-scope identifier")
        return dict(row)

    def audit(self, event, payload, run_id=None):
        prev = self.db.execute(
            "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        prev = prev[0] if prev else "0" * 64
        body = dump(payload)
        key = sha(dump([run_id, event, body, prev]))
        self.db.execute(
            "INSERT INTO audit_events(run_id,event_type,payload_json,prev_hash,event_hash) VALUES(?,?,?,?,?)",
            (run_id, event, body, prev, key),
        )

    def config(self):
        return json.loads(self.one("SELECT configuration_json FROM corpora")["configuration_json"])

    def snapshot(self, snapshot_id=None):
        if snapshot_id is None:
            head = self.db.execute("SELECT snapshot_id FROM current_snapshot WHERE singleton=1").fetchone()
            if head:
                return self.one("SELECT * FROM snapshots WHERE id=?", (head[0],))
            return self.one("SELECT * FROM snapshots ORDER BY created_at DESC,id DESC LIMIT 1")
        return self.one("SELECT * FROM snapshots WHERE id=?", (snapshot_id,))

    def manifest(self, snapshot_id=None):
        return json.loads(self.get(self.snapshot(snapshot_id)["manifest_sha"]))
