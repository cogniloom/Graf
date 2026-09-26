"""Durable source revisions, recoverable jobs, and current-revision evidence gate.

All long work happens outside workspace row locks. Publication and mutations
serialize on that row; a build can publish only its original revision. A separate
PostgreSQL session advisory lock elects one job executor across processes. Losing
that connection invalidates the executor before publication, including after a
crash/restart. No runtime DDL, generative clients, or writes to original sources.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from evidencekg.db import dump, sha
from evidencekg.hybrid.postgres import PostgresWorkset, _connect, _lock_id
from evidencekg.hybrid.runtime import Runtime, database_dsn, prepare

from .config import AppConfig
from .files import MAX_FILE_BYTES, inventory, stage


class NotReady(ValueError):
    pass


class Missing(ValueError):
    pass


def migrate(dsn):
    """Explicit atomic migration using an administrative DSN."""
    with _connect(dsn) as db, db.transaction():
        db.execute("SELECT pg_advisory_xact_lock(%s)", (_lock_id("docworm-schema-v1"),))
        db.execute(Path(__file__).with_name("schema.sql").read_text())


class Manager:
    def __init__(self, config: AppConfig, *, builder=None, runtime_factory=Runtime):
        self.config = config
        self.dsn = database_dsn(config.database_config)
        self.id = sha(str(config.home))
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.threads = []
        self.builder = builder or self._build
        self.runtime_factory = runtime_factory
        self._runtime = None
        self._runtime_path = None
        self._runtime_lock = threading.RLock()
        self._scan_lock = threading.Lock()
        config.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if config.home.stat().st_mode & 0o077:
            raise ValueError("Workspace home must be private (mode 0700)")
        with _connect(self.dsn) as db:
            db.execute(
                "INSERT INTO public.docworm_workspace(id,home) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (self.id, str(config.home)),
            )

    def _row(self, db, lock=False):
        return db.execute(
            "SELECT * FROM public.docworm_workspace WHERE id=%s" + (" FOR UPDATE" if lock else ""), (self.id,)
        ).fetchone()

    def _sources(self, db):
        return db.execute(
            "SELECT * FROM public.docworm_sources WHERE workspace=%s AND NOT removed ORDER BY id", (self.id,)
        ).fetchall()

    def _bump(self, db, message):
        row = db.execute(
            "UPDATE public.docworm_workspace SET revision=revision+1, state='updating', "
            "phase='queued', message=%s WHERE id=%s RETURNING revision",
            (message, self.id),
        ).fetchone()
        db.execute(
            "UPDATE public.docworm_jobs SET state='superseded', phase='superseded', finished_at=now() "
            "WHERE workspace=%s AND state='queued'",
            (self.id,),
        )
        db.execute(
            "INSERT INTO public.docworm_jobs(id,workspace,revision) VALUES(%s,%s,%s)",
            (uuid.uuid4().hex, self.id, row["revision"]),
        )
        self.wake.set()
        return row["revision"]

    @staticmethod
    def _source_view(row):
        return {k: row[k] for k in ("id", "path", "kind", "enabled", "status", "file_count", "error")}

    def sources(self):
        with _connect(self.dsn) as db:
            return {
                "items": [self._source_view(r) for r in self._sources(db)],
                "allowed_roots": [str(p) for p in self.config.allowed_roots],
            }

    def register(self, path):
        path = self.config.source(path)
        kind = "directory" if path.is_dir() else "file"
        with _connect(self.dsn) as db, db.transaction():
            self._row(db, True)
            for row in self._sources(db):
                other = Path(row["path"])
                if path == other:
                    return self._source_view(row)
                if path.is_relative_to(other) or other.is_relative_to(path):
                    raise ValueError("Sources must not overlap; register the common directory once")
            row = db.execute(
                "INSERT INTO public.docworm_sources(id,workspace,path,kind) VALUES(%s,%s,%s,%s) RETURNING *",
                (uuid.uuid4().hex, self.id, str(path), kind),
            ).fetchone()
            self._bump(db, "Source added; evidence is gated until the new revision is ready.")
        return self._source_view(row)

    def change(self, source_id, *, enabled=None, remove=False, rescan=False):
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with _connect(self.dsn) as db, db.transaction():
            self._row(db, True)
            row = db.execute(
                "SELECT * FROM public.docworm_sources WHERE id=%s AND workspace=%s AND NOT removed",
                (source_id, self.id),
            ).fetchone()
            if row is None:
                raise Missing("Unknown source")
            if remove:
                db.execute(
                    "UPDATE public.docworm_sources SET removed=true, enabled=false, status='removed' WHERE id=%s",
                    (source_id,),
                )
            elif enabled is not None:
                db.execute(
                    "UPDATE public.docworm_sources SET enabled=%s,status=%s WHERE id=%s",
                    (enabled, "pending" if enabled else "paused", source_id),
                )
                row.update(enabled=enabled, status="pending" if enabled else "paused")
            elif rescan:
                db.execute(
                    "UPDATE public.docworm_sources SET inventory=NULL,status='pending' WHERE id=%s",
                    (source_id,),
                )
            self._bump(db, "Source selection changed; rebuilding the current revision.")
        return {"success": True} if remove or rescan else self._source_view(row)

    def rebuild(self):
        with _connect(self.dsn) as db, db.transaction():
            self._row(db, True)
            self._bump(db, "Rebuild requested.")
        return {"success": True}

    def reconcile(self):
        """Checksums are authoritative; same-size/mtime edits still invalidate evidence.

        Also invoked at evidence request boundaries, not just periodically. A scan
        racing a source mutation is discarded; it cannot overwrite newer state.
        """
        with self._scan_lock:
            with _connect(self.dsn) as db:
                expected = self._row(db)["revision"]
                sources = self._sources(db)
            changes = []
            for source in sources:
                if not source["enabled"]:
                    continue
                try:
                    current = inventory(self.config, source, self.stop_event.is_set)
                    error = None
                except InterruptedError:
                    raise
                except (OSError, ValueError) as exc:
                    current, error = {}, f"{type(exc).__name__}: {exc}"
                if current != source["inventory"] or error != source["error"]:
                    changes.append(
                        (dump(current), error, len(current), "blocked" if error else "pending", source["id"])
                    )
            if changes:
                with _connect(self.dsn) as db, db.transaction():
                    if self._row(db, True)["revision"] != expected:
                        return False
                    for values in changes:
                        db.execute(
                            "UPDATE public.docworm_sources SET inventory=%s::jsonb,error=%s,file_count=%s,status=%s "
                            "WHERE id=%s",
                            values,
                        )
                    self._bump(db, "Source content changed; verifying a new snapshot.")
            return True

    def jobs(self):
        with _connect(self.dsn) as db:
            rows = db.execute(
                "SELECT id,state,phase,started_at,finished_at,error,progress,revision,attempts "
                "FROM public.docworm_jobs WHERE workspace=%s ORDER BY revision DESC LIMIT 100",
                (self.id,),
            ).fetchall()
        return {"items": rows}

    def models_ready(self):
        return all(
            (self.config.models / name / "pinned-revision.json").is_file()
            and (self.config.models / name / "config.json").is_file()
            and any(
                (self.config.models / name / w).is_file() for w in ("model.safetensors", "pytorch_model.bin")
            )
            for name in ("dense", "reranker")
        )

    def status(self):
        with _connect(self.dsn) as db:
            row, sources = self._row(db), self._sources(db)
        ready = row["revision"] == row["published_revision"] and row["state"] in ("ready", "ready_with_gaps")
        counts = {
            "sources": len(sources),
            "documents": 0,
            "passages": 0,
            "connections": 0,
            "gaps": sum(bool(s["error"]) for s in sources if s["enabled"]),
        }
        if ready:
            counts.update(row["counts"])
            counts["sources"] = len(sources)
        return {
            "workspace_name": self.config.workspace_name or self.config.home.name,
            "state": row["state"],
            "phase": row["phase"],
            "revision": row["revision"],
            "published_revision": row["published_revision"],
            "snapshot_id": row["snapshot_id"] if ready else None,
            "counts": counts,
            "jobs": self.jobs()["items"],
            "message": row["message"],
            "models_ready": self.models_ready(),
            "device": self.config.device,
        }

    def _progress(self, job, phase, **counts):
        if self.stop_event.is_set():
            raise InterruptedError("Application is stopping; job will resume on restart")
        with _connect(self.dsn) as db, db.transaction():
            if self._row(db, True)["revision"] != job["revision"]:
                raise NotReady("Build superseded by a newer source revision")
            active = db.execute(
                "UPDATE public.docworm_jobs SET phase=%s,progress=%s::jsonb WHERE id=%s "
                "AND state='running' AND attempts=%s RETURNING id",
                (phase, dump(counts), job["id"], job["attempts"]),
            ).fetchone()
            if active is None:
                raise NotReady("Job executor was replaced; stale progress discarded")
            db.execute("UPDATE public.docworm_workspace SET phase=%s WHERE id=%s", (phase, self.id))

    def _build(self, job, sources, progress):
        from evidencekg.config import initialize
        from evidencekg.ingest import ingest

        generation = self.config.home / "generations" / (job["id"] + "-" + uuid.uuid4().hex)
        staging, state = generation / "staging", generation / "vault"
        staging.mkdir(parents=True, mode=0o700)
        copied = 0
        for source in sources:
            copied += stage(self.config, source, staging, self.stop_event.is_set)
            progress("staging", files=copied, total_files=sum(s["file_count"] for s in sources))
        progress("ingesting", files=copied)
        # File acquisition is 100 MB; max_input_bytes is the existing segment/
        # context budget and must not be confused with a file-size ceiling.
        store = initialize(state, staging, {"max_file_bytes": MAX_FILE_BYTES})
        try:
            snapshot = ingest(store)
            manifest = store.manifest(snapshot)
        finally:
            store.close()
        progress("preparing", documents=len(manifest["documents"]))
        prepared = prepare(
            state,
            self.config.models,
            self.config.database_config,
            snapshot=snapshot,
            device=self.config.device,
            output=generation / "hybrid.json",
        )
        with PostgresWorksetContext(self.dsn) as ledger:
            verified, segments, links = ledger.load_snapshot(snapshot)
        gaps = sum(
            bool(d["warnings"]) or d["status"] != "ready" or d["empty"] for d in verified["documents"]
        ) + len(verified.get("inventory_errors", []))
        return {
            "snapshot_id": snapshot,
            "state": str(state),
            "config": prepared["config"],
            "sources": [{k: s[k] for k in ("id", "path", "kind", "inventory")} for s in sources],
            "counts": {
                "documents": len(verified["documents"]),
                "passages": len(segments),
                "connections": len(links),
                "gaps": gaps,
            },
        }

    def run_once(self):
        """Try one job; recover abandoned running jobs only while holding election lock."""
        if self.stop_event.is_set():
            return False
        self.reconcile()
        with _connect(self.dsn) as leader:
            locked = leader.execute(
                "SELECT pg_try_advisory_lock(%s) AS locked", (_lock_id("docworm-worker:" + self.id),)
            ).fetchone()["locked"]
            if not locked:
                return False
            with leader.transaction():
                row = self._row(leader, True)
                leader.execute(
                    "UPDATE public.docworm_jobs SET state='superseded',phase='superseded',finished_at=now() "
                    "WHERE workspace=%s AND revision<>%s AND state IN ('running','queued')",
                    (self.id, row["revision"]),
                )
                leader.execute(
                    "UPDATE public.docworm_jobs SET state='queued',phase='recovered', "
                    "error='Previous executor interrupted; retrying from immutable inputs' "
                    "WHERE workspace=%s AND revision=%s AND state='running'",
                    (self.id, row["revision"]),
                )
                job = leader.execute(
                    "UPDATE public.docworm_jobs SET state='running',phase='inventory',started_at=now(),"
                    "finished_at=NULL,error=NULL,attempts=attempts+1 WHERE workspace=%s AND revision=%s "
                    "AND state='queued' RETURNING *",
                    (self.id, row["revision"]),
                ).fetchone()
                if job is None:
                    return False
                sources = [s for s in self._sources(leader) if s["enabled"]]
            try:
                if any(s["error"] or s["inventory"] is None for s in sources):
                    raise NotReady(
                        "Source inventory is incomplete; fix missing or unreadable sources, then rescan."
                    )
                self._progress(job, "staging", files=0, total_files=sum(s["file_count"] for s in sources))
                publication = (
                    self.builder(job, sources, lambda phase, **kw: self._progress(job, phase, **kw))
                    if sources
                    else None
                )
                self.reconcile()
                if self.stop_event.is_set():
                    raise InterruptedError("Application stopped before publication")
                # Publication uses the election connection: a lost lease can never publish.
                with leader.transaction():
                    current = self._row(leader, True)
                    if current["revision"] != job["revision"]:
                        raise NotReady("Build superseded by a newer source revision")
                    state = (
                        ("ready_with_gaps" if publication["counts"]["gaps"] else "ready")
                        if publication
                        else "empty"
                    )
                    leader.execute(
                        "UPDATE public.docworm_workspace SET published_revision=%s,state=%s,phase=%s,"
                        "snapshot_id=%s,publication=%s::jsonb,counts=%s::jsonb,message=%s WHERE id=%s",
                        (
                            job["revision"],
                            state,
                            "idle",
                            publication["snapshot_id"] if publication else None,
                            dump(publication),
                            dump(publication["counts"] if publication else {}),
                            "Current sources are ready."
                            if publication
                            else "Add or resume a local source to begin.",
                            self.id,
                        ),
                    )
                    leader.execute(
                        "UPDATE public.docworm_jobs SET state='succeeded',phase='complete',finished_at=now() "
                        "WHERE id=%s",
                        (job["id"],),
                    )
                    leader.execute(
                        "UPDATE public.docworm_sources SET status='ready' WHERE workspace=%s AND enabled "
                        "AND NOT removed",
                        (self.id,),
                    )
                return True
            except Exception as exc:
                # A separate connection records failure even if the election connection died.
                # Do not overwrite a successor's recovery or completion after connection loss.
                with _connect(self.dsn) as db, db.transaction():
                    current = self._row(db, True)
                    state = (
                        "superseded"
                        if current["revision"] != job["revision"]
                        else "interrupted"
                        if isinstance(exc, InterruptedError)
                        else "failed"
                    )
                    result = db.execute(
                        "UPDATE public.docworm_jobs SET state=%s,phase=%s,error=%s,finished_at=now() "
                        "WHERE id=%s AND state='running' AND attempts=%s RETURNING id",
                        (state, state, str(exc)[:2000], job["id"], job["attempts"]),
                    ).fetchone()
                    if result and state != "superseded":
                        db.execute(
                            "UPDATE public.docworm_workspace SET state='blocked',phase=%s,message=%s WHERE id=%s",
                            (state, str(exc)[:2000], self.id),
                        )
                return False

    def start(self):
        if self.threads:
            return
        self.stop_event.clear()
        # Graceful interruptions, unlike failures, retry automatically on restart.
        with _connect(self.dsn) as db:
            db.execute(
                "UPDATE public.docworm_jobs SET state='queued' WHERE workspace=%s AND state='interrupted'",
                (self.id,),
            )

        def watch():
            while not self.stop_event.is_set():
                try:
                    self.reconcile()
                except Exception:
                    # Requests still fail closed on DB/scan errors; retry next poll.
                    pass
                self.stop_event.wait(self.config.scan_interval_seconds)

        def work():
            while not self.stop_event.is_set():
                self.wake.clear()
                try:
                    worked = self.run_once()
                except Exception:
                    worked = False
                if not worked:
                    self.wake.wait(min(self.config.scan_interval_seconds, 2))

        self.threads = [
            threading.Thread(target=fn, name="graf-" + name, daemon=True)
            for fn, name in ((watch, "watch"), (work, "jobs"))
        ]
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        for thread in self.threads:
            thread.join(timeout=5)
        # Keep live thread references: start() cannot spawn a duplicate executor.
        self.threads = [t for t in self.threads if t.is_alive()]
        with self._runtime_lock:
            if self._runtime:
                self._runtime.close()
                self._runtime = None

    def _ready(self):
        if not self.reconcile():
            raise NotReady("Source revision changed during verification; retry after rebuilding")
        with _connect(self.dsn) as db:
            row = self._row(db)
        if row["state"] not in ("ready", "ready_with_gaps") or row["published_revision"] != row["revision"]:
            raise NotReady("Current source revision is not ready")
        return row

    def evidence(self, operation):
        """Pre/post source hashing and revision fence cover every evidence route."""
        before = self._ready()
        result = operation(before)
        after = self._ready()
        if after["revision"] != before["revision"] or after["snapshot_id"] != before["snapshot_id"]:
            raise NotReady("Sources changed while reading; response discarded")
        return result

    def _with_runtime(self, row, call):
        with self._runtime_lock:
            publication = row["publication"]
            if self._runtime is None or self._runtime_path != publication["config"]:
                if self._runtime:
                    self._runtime.close()
                self._runtime = self.runtime_factory(publication["state"], publication["config"])
                self._runtime_path = publication["config"]
            return call(self._runtime)

    def search(self, question, limit=12):
        def perform(row):
            result = self._with_runtime(row, lambda runtime: runtime.retrieve(question, limit=limit))
            with _connect(self.dsn) as db, db.transaction():
                if self._row(db, True)["revision"] != row["revision"]:
                    raise NotReady("Sources changed during search")
                db.execute(
                    "INSERT INTO public.docworm_worksets(id,workspace,revision,snapshot_id) VALUES(%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (result["workset_id"], self.id, row["revision"], row["snapshot_id"]),
                )
            return self._paths(result, row)

        return self.evidence(perform)

    def workset(self, key, cursor=None, limit=100):
        def perform(row):
            with _connect(self.dsn) as db:
                known = db.execute(
                    "SELECT id FROM public.docworm_worksets WHERE id=%s AND workspace=%s AND revision=%s "
                    "AND snapshot_id=%s",
                    (key, self.id, row["revision"], row["snapshot_id"]),
                ).fetchone()
            if known is None:
                raise Missing("Workset is not part of the current source revision")
            return self._paths(
                self._with_runtime(row, lambda r: r.page(key, cursor=cursor, limit=limit)), row
            )

        return self.evidence(perform)

    def _snapshot(self, row):
        with PostgresWorksetContext(self.dsn) as ledger:
            return ledger.load_snapshot(row["snapshot_id"])

    @staticmethod
    def _path(path, row):
        for source in row["publication"]["sources"]:
            prefix = source["id"] + "/"
            if path.startswith(prefix):
                root = Path(source["path"]) if source["kind"] == "directory" else Path(source["path"]).parent
                return str(root / path[len(prefix) :])
        return path

    def _paths(self, value, row):
        if isinstance(value, list):
            return [self._paths(v, row) for v in value]
        if isinstance(value, dict):
            return {
                k: self._path(v, row)
                if k in ("path", "source_path") and isinstance(v, str)
                else self._paths(v, row)
                for k, v in value.items()
            }
        return value

    def documents(self, query="", offset=0, limit=50):
        def perform(row):
            manifest, segments, _ = self._snapshot(row)
            counts = {}
            for s in segments.values():
                counts[s["document_version_id"]] = counts.get(s["document_version_id"], 0) + 1
            docs = [
                {
                    "id": d["document_version_id"],
                    "path": self._path(d["path"], row),
                    "status": d["status"],
                    "warnings": d["warnings"],
                    "passage_count": counts.get(d["document_version_id"], 0),
                }
                for d in manifest["documents"]
                if query.casefold() in self._path(d["path"], row).casefold()
            ]
            return {"items": docs[offset : offset + limit], "total": len(docs)}

        return self.evidence(perform)

    def document(self, key, offset=0, limit=50):
        def perform(row):
            manifest, segments, links = self._snapshot(row)
            doc = next((d for d in manifest["documents"] if d["document_version_id"] == key), None)
            if doc is None:
                raise Missing("Unknown current document")
            parts = sorted(
                (s for s in segments.values() if s["document_version_id"] == key), key=lambda s: s["ordinal"]
            )
            relationships = [e for e in links if key in (e["from_node"], e["to_node"])]
            return {
                "id": key,
                "path": self._path(doc["path"], row),
                "status": doc["status"],
                "warnings": doc["warnings"],
                "passages": [
                    {k: s[k] for k in ("id", "text", "locators")} for s in parts[offset : offset + limit]
                ],
                "passage_count": len(parts),
                "next_offset": offset + limit if offset + limit < len(parts) else None,
                "relationships": relationships[:1000],
                "relationships_total": len(relationships),
                "relationships_truncated": len(relationships) > 1000,
            }

        return self.evidence(perform)

    def graph(self, limit=2000):
        def perform(row):
            manifest, _, links = self._snapshot(row)
            nodes = {
                d["document_version_id"]: {
                    "id": d["document_version_id"],
                    "label": self._path(d["path"], row),
                    "kind": "document",
                    "document_id": d["document_version_id"],
                }
                for d in manifest["documents"]
            }
            # Unresolved relationships have no endpoint and are shown in document details.
            edges = [
                {"id": e["id"], "source": e["from_node"], "target": e["to_node"], "type": e["relation_type"]}
                for e in links
                if e["from_node"] in nodes and e["to_node"] in nodes
            ]
            selected = list(nodes.values())[:limit]
            ids = {n["id"] for n in selected}
            selected_edges = [e for e in edges if e["source"] in ids and e["target"] in ids][: limit * 4]
            return {
                "nodes": selected,
                "edges": selected_edges,
                "total_nodes": len(nodes),
                "total_edges": len(edges),
                "truncated": len(selected) < len(nodes) or len(selected_edges) < len(edges),
                "snapshot_id": row["snapshot_id"],
            }

        return self.evidence(perform)


class PostgresWorksetContext:
    def __init__(self, dsn):
        self.dsn = dsn

    def __enter__(self):
        self.ledger = PostgresWorkset(self.dsn)
        return self.ledger

    def __exit__(self, *args):
        self.ledger.close()
