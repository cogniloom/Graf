"""Private, durable storage for Graf-owned investigation artifacts.

``Vault(path)`` owns only its directory. Bytes live in immutable, hash-addressed
files; artifact names are metadata, never paths. The append-only ledger contains
opaque references, hashes and attribution, not artifact content or deletion
reasons. Callers must likewise use opaque IDs in event payloads and run/actor
identifiers. A deletion reason is represented only by its SHA-256 digest.

All operations serialize across threads/processes using flock and SQLite FULL
transactions. Deletion commits intent before unlink, then commits tombstones and
result; opening the vault completes interrupted intents. Uncommitted additions
are removed on recovery. This is logical local deletion, not physical SSD erase.

Signatures prove integrity under a key, not evidence truth, completeness, or
freshness. Keep a separately obtained public key/checkpoint outside the vault to
anchor trust/history; an attacker controlling this OS account also controls the
local signing key. Exported historical copies cannot be remotely erased.

This implementation targets the project's POSIX local runtime (fcntl/O_NOFOLLOW).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from .verifier import (
    FORMAT,
    canonical,
    content_name,
    digest,
    ledger_state,
    validate_records,
    verify_signature,
)

_RESERVED = {"artifact_added", "relationship_added", "deletion_intent", "deletion_result"}
_TOKEN = re.compile(r"^[A-Za-z0-9_.:@/-]{1,256}$")
_OBJECT = re.compile(r"^[0-9a-f]{32}-[0-9a-f]{64}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(seq INTEGER PRIMARY KEY, record TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
 BEGIN SELECT RAISE(ABORT, 'append-only events'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
 BEGIN SELECT RAISE(ABORT, 'append-only events'); END;
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _token(value, label):
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError(f"{label} must be a nonempty opaque token (maximum 256 characters)")
    return value


def _directory(path, *, create=False):
    """Walk every component with directory FDs: no symlink parent traversal."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("parent traversal is forbidden")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _regular(fd, *, private=False):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("vault file must be a regular file with a single link")
    if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("vault file must be private to its owner")
    return info


def _read(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        _regular(fd, private=True)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _write_new(directory, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory)


class Vault:
    """See method docstrings for the service API; returned dictionaries are copies."""

    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        fd = _directory(self.path, create=True)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("vault directory must be owned by this user with mode 0700")
            self._identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)
        with self._session(initialize=True):
            pass

    @contextmanager
    def _session(self, *, initialize=False):
        root = _directory(self.path)
        lock = objects = None
        db = None
        try:
            info = os.fstat(root)
            if (info.st_dev, info.st_ino) != self._identity or info.st_mode & 0o077:
                raise ValueError("vault directory changed or permissions are unsafe")
            lock = os.open(
                "writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=root
            )
            _regular(lock, private=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if initialize:
                try:
                    os.mkdir("objects", 0o700, dir_fd=root)
                    os.fsync(root)
                except FileExistsError:
                    pass
            objects = os.open("objects", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            obj_info = os.fstat(objects)
            if obj_info.st_uid != os.getuid() or obj_info.st_mode & 0o077:
                raise ValueError("objects directory permissions are unsafe")
            key_created = False
            try:
                raw_key = _read(root, "signing.key")
            except FileNotFoundError:
                if not initialize or self._exists(root, "ledger.sqlite3"):
                    raise ValueError("vault signing key is missing") from None
                raw_key = Ed25519PrivateKey.generate().private_bytes(
                    Encoding.Raw, PrivateFormat.Raw, NoEncryption()
                )
                temporary = "key-" + uuid.uuid4().hex
                _write_new(root, temporary, raw_key)
                os.rename(temporary, "signing.key", src_dir_fd=root, dst_dir_fd=root)
                os.fsync(root)
                key_created = True
            key = Ed25519PrivateKey.from_private_bytes(raw_key)
            exists = self._exists(root, "ledger.sqlite3")
            if not exists and not key_created:
                raise ValueError("vault database is missing")
            # SQLite opens its own files. Fence every existing database/sidecar,
            # and anchor the directory through an open FD on Linux. The private
            # directory excludes other users; same-account hostile writers are
            # outside the signing-key trust boundary documented above.
            for name in (
                "ledger.sqlite3",
                "ledger.sqlite3-journal",
                "ledger.sqlite3-wal",
                "ledger.sqlite3-shm",
            ):
                if self._exists(root, name):
                    check = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
                    try:
                        _regular(check, private=True)
                    finally:
                        os.close(check)
            if not exists:
                _write_new(root, "ledger.sqlite3", b"")
            location = (
                f"/proc/self/fd/{root}/ledger.sqlite3"
                if Path("/proc/self/fd").is_dir()
                else str(self.path / "ledger.sqlite3")
            )
            db = sqlite3.connect(location, timeout=30, isolation_level=None)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            if not exists:
                db.executescript(_SCHEMA)
                db.execute("BEGIN IMMEDIATE")
                self._seal(db, key)
                db.commit()
                os.fsync(root)
            self._validate(db, key, allow_pending=True)
            self._recover(db, objects, key)
            self._cleanup(db, objects)
            yield db, objects, key
        finally:
            if db is not None:
                db.close()
            if objects is not None:
                os.close(objects)
            if lock is not None:
                os.close(lock)
            os.close(root)

    @staticmethod
    def _exists(directory, name):
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _events(db):
        return [json.loads(row[0]) for row in db.execute("SELECT record FROM events ORDER BY seq")]

    @staticmethod
    def _artifacts(db):
        return [json.loads(row[0]) for row in db.execute("SELECT record FROM artifacts ORDER BY rowid")]

    @staticmethod
    def _links(db):
        return [
            {**e["payload"], "run_id": e["run_id"]}
            for e in Vault._events(db)
            if e["event_type"] == "relationship_added"
        ]

    @staticmethod
    def _envelope(payload, key):
        return {
            "payload": payload,
            "public_key": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
            "signature": key.sign(canonical(payload)).hex(),
        }

    @staticmethod
    def _append(db, event_type, actor, run_id, payload):
        last = db.execute("SELECT seq,record FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        event = {
            "seq": last[0] + 1 if last else 1,
            "previous": json.loads(last[1])["hash"] if last else "0" * 64,
            "event_type": event_type,
            "actor": actor,
            "run_id": run_id,
            "payload": payload,
            "time": _now(),
        }
        event["hash"] = digest(event)
        db.execute("INSERT INTO events VALUES(?,?)", (event["seq"], canonical(event).decode()))
        return event

    @staticmethod
    def _seal(db, key):
        events = Vault._events(db)
        envelope = Vault._envelope(
            {
                "format": FORMAT,
                "event_count": len(events),
                "head": events[-1]["hash"] if events else "0" * 64,
                "time": _now(),
            },
            key,
        )
        db.execute(
            "INSERT OR IGNORE INTO checkpoints VALUES(?,?)", (len(events), canonical(envelope).decode())
        )
        return json.loads(
            db.execute("SELECT record FROM checkpoints WHERE seq=?", (len(events),)).fetchone()[0]
        )

    @staticmethod
    def _validate(db, key, *, allow_pending=False):
        events = Vault._events(db)
        head = validate_records(events, Vault._artifacts(db), Vault._links(db), allow_pending=allow_pending)
        row = db.execute("SELECT seq,record FROM checkpoints ORDER BY seq DESC LIMIT 1").fetchone()
        if not row:
            raise ValueError("missing signed checkpoint")
        payload, public = verify_signature(json.loads(row[1]))
        if (
            public != key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            or payload["format"] != FORMAT
            or row[0] != len(events)
            or payload["event_count"] != len(events)
            or payload["head"] != head
        ):
            raise ValueError("ledger does not match signed checkpoint")

    @contextmanager
    def _transaction(self, db, key):
        db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._seal(db, key)
            db.commit()
        except BaseException:
            db.rollback()
            raise

    @staticmethod
    def _lookup(db, artifact_id):
        row = db.execute("SELECT record FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError(artifact_id)
        return json.loads(row[0])

    @staticmethod
    def _bytes(objects, artifact):
        if artifact["deleted"]:
            raise FileNotFoundError("artifact has been erased")
        value = _read(objects, content_name(artifact).split("/")[1])
        if len(value) != artifact["size"] or hashlib.sha256(value).hexdigest() != artifact["sha256"]:
            raise ValueError("artifact content hash or size mismatch")
        return value

    def add_artifact(
        self,
        data: bytes,
        kind: str,
        name: str,
        media_type: str,
        run_id: str,
        parents=(),
        *,
        actor="system:vault",
    ) -> dict:
        """Store immutable bytes, metadata and derived_from parent links atomically."""
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        for value, label in ((kind, "kind"), (run_id, "run_id"), (actor, "actor")):
            _token(value, label)
        if any(not isinstance(v, str) or not v or len(v) > 1024 or "\x00" in v for v in (name, media_type)):
            raise ValueError("name/media_type must be nonempty bounded metadata")
        if isinstance(parents, str):
            raise TypeError("parents must be an iterable of artifact IDs")
        parents = list(dict.fromkeys(parents))
        with self._session() as (db, objects, key):
            for parent in parents:
                previous = self._lookup(db, parent)
                if previous["deleted"] or previous["run_id"] != run_id:
                    raise ValueError("parents must be retained artifacts in this run")
                self._bytes(objects, previous)
            artifact = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "name": name,
                "media_type": media_type,
                "run_id": run_id,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "parents": parents,
                "deleted": False,
                "created_at": _now(),
            }
            filename = content_name(artifact).split("/")[1]
            # The database transaction publishes this file only after its data
            # and directory entry are durable; a pre-commit crash leaves an orphan.
            _write_new(objects, filename, data)
            with self._transaction(db, key):
                db.execute(
                    "INSERT INTO artifacts VALUES(?,?)", (artifact["id"], canonical(artifact).decode())
                )
                self._append(
                    db,
                    "artifact_added",
                    actor,
                    run_id,
                    {k: artifact[k] for k in ("id", "sha256", "size", "parents")}
                    | {"metadata_hash": digest(artifact)},
                )
                for parent in parents:
                    self._append(
                        db,
                        "relationship_added",
                        actor,
                        run_id,
                        {
                            "id": uuid.uuid4().hex,
                            "source": artifact["id"],
                            "target": parent,
                            "relation": "derived_from",
                        },
                    )
            return artifact

    def artifact(self, artifact_id: str) -> dict:
        """Return retained metadata or a minimal tombstone (``deleted=True``)."""
        with self._session() as (db, _, _key):
            return self._lookup(db, artifact_id)

    def read_artifact(self, artifact_id: str) -> bytes:
        """Read and hash-check a retained artifact; erased content raises FileNotFoundError."""
        with self._session() as (db, objects, _key):
            return self._bytes(objects, self._lookup(db, artifact_id))

    def append_event(self, event_type: str, actor: str, run_id: str, payload: dict) -> dict:
        """Append an attributed event with bounded, flat opaque references/counts.

        Content, free-text reasons, questions and names belong in artifacts. String
        values are restricted to token syntax; this is structural minimization,
        not a detector for sensitive identifiers supplied by a caller.
        """
        for value, label in ((event_type, "event_type"), (actor, "actor"), (run_id, "run_id")):
            _token(value, label)
        if event_type in _RESERVED:
            raise ValueError("reserved event type")
        if not isinstance(payload, dict) or len(canonical(payload)) > 4096:
            raise ValueError("event payload must be a small dictionary")
        for name, value in payload.items():
            _token(name, "payload key")
            if name.lower() in {"text", "content", "reason", "prompt", "question", "name", "body", "secret"}:
                raise ValueError("sensitive event fields belong in an artifact")
            if isinstance(value, str):
                _token(value, "payload value")
            elif value is not None and type(value) not in (bool, int):
                raise ValueError("event values must be opaque tokens, integers, booleans or null")
        with self._session() as (db, _, key):
            with self._transaction(db, key):
                return self._append(db, event_type, actor, run_id, payload)

    def link(self, source: str, target: str, relation: str, run_id: str, *, actor="system:vault") -> dict:
        """Append a relationship; derived_from also participates in erasure closure."""
        for value, label in ((relation, "relation"), (run_id, "run_id"), (actor, "actor")):
            _token(value, label)
        with self._session() as (db, _, key):
            for aid in (source, target):
                artifact = self._lookup(db, aid)
                if artifact["deleted"] or artifact["run_id"] != run_id:
                    raise ValueError("relationship endpoints must be retained artifacts in this run")
            link = {"id": uuid.uuid4().hex, "source": source, "target": target, "relation": relation}
            with self._transaction(db, key):
                self._append(db, "relationship_added", actor, run_id, link)
            return {**link, "run_id": run_id}

    def events(self, run_id=None) -> list[dict]:
        with self._session() as (db, _, _key):
            return [e for e in self._events(db) if run_id is None or e["run_id"] == run_id]

    def artifacts(self, run_id=None) -> list[dict]:
        with self._session() as (db, _, _key):
            return [a for a in self._artifacts(db) if run_id is None or a["run_id"] == run_id]

    def relationships(self, run_id=None) -> list[dict]:
        with self._session() as (db, _, _key):
            return [r for r in self._links(db) if run_id is None or r["run_id"] == run_id]

    def checkpoint(self) -> dict:
        """Return the latest persisted Ed25519 envelope, after verifying retained bytes."""
        with self._session() as (db, objects, key):
            for artifact in self._artifacts(db):
                if not artifact["deleted"]:
                    self._bytes(objects, artifact)
            return self._seal(db, key)

    def verify(self) -> dict:
        """Validate local ledger, metadata and all retained bytes; no identity trust claim."""
        try:
            checkpoint = self.checkpoint()
            return {
                "valid": True,
                "integrity": True,
                "trusted": False,
                "errors": [],
                "checkpoint": checkpoint,
            }
        except Exception as exc:
            return {
                "valid": False,
                "integrity": False,
                "trusted": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }

    @staticmethod
    def _closure(db, ids):
        if isinstance(ids, str):
            raise TypeError("ids must be an iterable of artifact IDs")
        selected = set(ids)
        if not selected:
            raise ValueError("at least one erasure target is required")
        artifacts = {a["id"]: a for a in Vault._artifacts(db)}
        if not selected <= artifacts.keys():
            raise KeyError("unknown erasure target")
        if any(artifacts[a]["deleted"] for a in selected):
            raise ValueError("selected content is already erased")
        links = Vault._links(db)
        while True:
            before = set(selected)
            selected.update(
                a for a, m in artifacts.items() if not m["deleted"] and selected.intersection(m["parents"])
            )
            selected.update(
                r["source"]
                for r in links
                if r["relation"] == "derived_from"
                and r["target"] in selected
                and not artifacts[r["source"]]["deleted"]
            )
            if before == selected:
                break
        return sorted(selected)

    def preview_erase(self, ids) -> dict:
        """Return current transitive impact; erase recomputes under the writer lock."""
        with self._session() as (db, _, _key):
            selected = self._closure(db, ids)
            return {
                "ids": selected,
                "artifact_ids": selected,
                "affected_runs": sorted({self._lookup(db, a)["run_id"] for a in selected}),
            }

    @staticmethod
    def _unlink(objects, filename):
        # Refuse nonregular/hard-linked/symlink targets instead of touching them.
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=objects)
        except FileNotFoundError:
            return
        try:
            _regular(fd, private=True)
        finally:
            os.close(fd)
        os.unlink(filename, dir_fd=objects)
        os.fsync(objects)

    def _finish_deletion(self, db, objects, key, intent, *, recovered):
        payload = intent["payload"]
        for aid in payload["ids"]:
            self._unlink(objects, content_name(self._lookup(db, aid)).split("/")[1])
        with self._transaction(db, key):
            for aid in payload["ids"]:
                artifact = self._lookup(db, aid)
                tombstone = {k: artifact[k] for k in ("id", "sha256", "size", "run_id", "parents")}
                tombstone["deleted"] = True
                db.execute("UPDATE artifacts SET record=? WHERE id=?", (canonical(tombstone).decode(), aid))
            self._append(
                db,
                "deletion_result",
                intent["actor"],
                intent["run_id"],
                {
                    "operation": payload["operation"],
                    "ids": payload["ids"],
                    "status": "deleted",
                    "recovered": recovered,
                },
            )
        return {
            "operation": payload["operation"],
            "ids": payload["ids"],
            "artifact_ids": payload["ids"],
            "status": "deleted",
            "recovered": recovered,
            "limitations": "Local payloads unlinked; physical media, external copies and backups are not erased.",
        }

    def erase(self, ids, actor: str, reason: str) -> dict:
        """Durably record intent, erase transitive derivatives and retain minimal tombstones.

        ``reason`` is hashed, never saved as plaintext; store the full instruction
        as a separately controlled artifact when required. Does not touch sources
        outside this vault. Interrupted intent is resumed by the next operation.
        """
        _token(actor, "actor")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("an attributed erasure reason is required")
        with self._session() as (db, objects, key):
            selected = self._closure(db, ids)
            # Missing/corrupt bytes must not be laundered into legitimate deletion.
            for aid in selected:
                self._bytes(objects, self._lookup(db, aid))
            with self._transaction(db, key):
                intent = self._append(
                    db,
                    "deletion_intent",
                    actor,
                    self._lookup(db, selected[0])["run_id"],
                    {
                        "operation": uuid.uuid4().hex,
                        "ids": selected,
                        "reason_sha256": hashlib.sha256(reason.encode()).hexdigest(),
                    },
                )
            return self._finish_deletion(db, objects, key, intent, recovered=False)

    def _recover(self, db, objects, key):
        events = self._events(db)
        _added, _deleted, intents, _links, _head = ledger_state(events)
        for event in events:
            if event["event_type"] == "deletion_intent" and event["payload"]["operation"] in intents:
                self._finish_deletion(db, objects, key, event, recovered=True)
        self._validate(db, key)

    def _cleanup(self, db, objects):
        artifacts = self._artifacts(db)
        expected = {content_name(a).split("/")[1]: a for a in artifacts}
        for filename in os.listdir(objects):
            if filename in expected:
                if expected[filename]["deleted"]:
                    raise ValueError("deleted artifact payload unexpectedly exists")
                continue
            if _OBJECT.fullmatch(filename) or re.fullmatch(r"tmp-[0-9a-f]{32}", filename):
                self._unlink(objects, filename)
            else:
                raise ValueError("unexpected object in vault")

    def export(self, run_id: str, destination: str | Path, extra_manifest=None) -> Path:
        """Atomically create a signed offline ZIP; never overwrite an existing path.

        Includes the whole minimal ledger and this run's metadata/payloads, so
        recipients learn other runs' opaque ledger references. Extra manifest
        fields are signed under ``extra`` and cannot override integrity fields.
        """
        _token(run_id, "run_id")
        destination = Path(destination).absolute()
        parent = _directory(destination.parent)
        temporary = "graf-export-" + uuid.uuid4().hex + ".tmp"
        try:
            if self._exists(parent, destination.name):
                raise FileExistsError(destination)
            with self._session() as (db, objects, key):
                artifacts = [a for a in self._artifacts(db) if a["run_id"] == run_id]
                events = self._events(db)
                if not artifacts and not any(e["run_id"] == run_id for e in events):
                    raise KeyError(run_id)
                manifest = {
                    "format": FORMAT,
                    "run_id": run_id,
                    "events": events,
                    "artifacts": artifacts,
                    "relationships": [r for r in self._links(db) if r["run_id"] == run_id],
                    "checkpoint": self._seal(db, key),
                    "extra": extra_manifest or {},
                }
                validate_records(events, artifacts, manifest["relationships"], run_id)
                envelope = self._envelope(manifest, key)
                fd = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
                )
                with os.fdopen(fd, "wb") as output:
                    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        archive.writestr("manifest.json", canonical(envelope))
                        for artifact in artifacts:
                            if not artifact["deleted"]:
                                archive.writestr(content_name(artifact), self._bytes(objects, artifact))
                    output.flush()
                    os.fsync(output.fileno())
                # link is atomic and refuses a concurrently created destination.
                os.link(
                    temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
                )
                os.unlink(temporary, dir_fd=parent)
                os.fsync(parent)
                return destination
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)
