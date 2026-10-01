"""Durable source revisions, recoverable jobs, and current-revision evidence gate.

All long work happens outside workspace row locks. Publication and mutations
serialize on that row; a build can publish only its original revision. A separate
PostgreSQL session advisory lock elects one job executor across processes. Losing
that connection invalidates the executor before publication, including after a
crash/restart. No runtime DDL, generative clients, or writes to original sources.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from evidencekg.db import dump, sha
from evidencekg.hybrid.postgres import PostgresWorkset, _connect, _lock_id
from evidencekg.hybrid.runtime import Runtime, database_dsn, prepare

from .config import AppConfig
from .files import inventory, restricted_hashes, restricted_paths, stage


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
        self._scan_requested = threading.Event()
        self.threads = []
        self.builder = builder or self._build
        self.runtime_factory = runtime_factory
        self._runtime = None
        self._runtime_path = None
        self._runtime_lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._scan_progress = None
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

    def request_reconcile(self):
        """Wake the existing watcher; repeated requests share one pending scan."""
        self._scan_requested.set()

    def reconcile(self):
        """Checksums are authoritative; same-size/mtime edits still invalidate evidence.

        Also invoked at evidence request boundaries, not just periodically. A scan
        racing a source mutation is discarded; it cannot overwrite newer state.
        """
        with self._scan_lock:
            self._scan_progress = {"files_checked": 0, "bytes_checked": 0}
            try:
                return self._reconcile()
            finally:
                self._scan_progress = None

    def _reconcile(self):
        with _connect(self.dsn) as db:
            workspace = self._row(db)
            expected = workspace["revision"]
            sources = self._sources(db)
        from evidencekg.knowledge import signature as knowledge_signature

        # Only queue an upgrade after the prior publication has settled;
        # never supersede an active scan merely because rules changed.
        knowledge_changed = (
            workspace["state"] in ("ready", "ready_with_gaps")
            and workspace["revision"] == workspace["published_revision"]
            and workspace.get("publication") is not None
            and workspace["publication"].get(
                "knowledge_request_signature", workspace["publication"].get("knowledge_signature")
            )
            != knowledge_signature()
        )
        changes = []
        checked_files = checked_bytes = 0
        for source in sources:
            if not source["enabled"]:
                continue
            scanned = {"files_checked": 0, "bytes_checked": 0}

            def scanning(*, files_checked, bytes_checked, current_file):
                scanned.update(files_checked=files_checked, bytes_checked=bytes_checked)
                self._scan_progress = {
                    "files_checked": checked_files + files_checked,
                    "bytes_checked": checked_bytes + bytes_checked,
                    "current_source": source["path"], "current_file": current_file,
                }

            scanning(files_checked=0, bytes_checked=0, current_file="")
            try:
                current = inventory(self.config, source, self.stop_event.is_set, progress=scanning)
                error = None
            except InterruptedError:
                raise
            except (OSError, ValueError) as exc:
                current, error = {}, f"{type(exc).__name__}: {exc}"
            checked_files += scanned["files_checked"]
            checked_bytes += scanned["bytes_checked"]
            if current != source["inventory"] or error != source["error"]:
                changes.append(
                    (dump(current), error, len(current), "blocked" if error else "pending", source["id"])
                )
        if changes or knowledge_changed:
            with _connect(self.dsn) as db, db.transaction():
                if self._row(db, True)["revision"] != expected:
                    return False
                for values in changes:
                    db.execute(
                        "UPDATE public.docworm_sources SET inventory=%s::jsonb,error=%s,file_count=%s,status=%s "
                        "WHERE id=%s",
                        values,
                    )
                self._bump(
                    db,
                    "Source content changed; verifying a new snapshot."
                    if changes
                    else "Knowledge rules or local parser models changed; rebuilding derived knowledge.",
                )
        return True

    def jobs(self):
        with _connect(self.dsn) as db:
            rows = db.execute(
                "SELECT id,state,phase,started_at,finished_at,error,"
                "progress - 'partial_publication' AS progress,revision,attempts "
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
            # Polling must not deserialize hundreds of thousands of source
            # checksums or historical publication inventories on every request.
            row = db.execute(
                "SELECT state,phase,revision,published_revision,snapshot_id,counts,message,"
                "CASE WHEN jsonb_typeof(publication) = 'object' THEN publication - 'sources' "
                "ELSE publication END AS publication FROM public.docworm_workspace WHERE id=%s",
                (self.id,),
            ).fetchone()
            sources = db.execute(
                "SELECT enabled,error FROM public.docworm_sources WHERE workspace=%s AND NOT removed",
                (self.id,),
            ).fetchall()
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
            "knowledge_restart_required": bool(
                row.get("publication")
                and row["publication"].get("knowledge_request_signature") is not None
                and row["publication"].get("knowledge_request_signature")
                != row["publication"].get("knowledge_signature")
            ),
            "state": row["state"],
            "phase": row["phase"],
            "revision": row["revision"],
            "published_revision": row["published_revision"],
            "snapshot_id": row["snapshot_id"] if ready else None,
            "counts": counts,
            "jobs": self.jobs()["items"],
            "source_scan": self._scan_progress,
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
            db.execute(
                "UPDATE public.docworm_workspace SET state='updating',phase=%s,message=%s WHERE id=%s",
                (
                    phase,
                    "Processing sources in the background. Saved sessions and workspace controls remain available.",
                    self.id,
                ),
            )

    def _build(self, job, sources, progress):
        from evidencekg.config import initialize
        from evidencekg.knowledge import signature as knowledge_signature

        from .ingestion import ingest_isolated

        generation = self.config.home / "generations" / (job["id"] + "-" + uuid.uuid4().hex)
        staging, state = generation / "staging", generation / "vault"
        staging.mkdir(parents=True, mode=0o700)
        copied = 0
        copied_bytes = 0
        total_files = sum(s["file_count"] for s in sources)
        total_bytes = sum(item["size"] for s in sources for item in s["inventory"].values())
        for source in sources:
            def staged(*, files, bytes_copied, current_file):
                progress(
                    "staging", files=copied + files, total_files=total_files,
                    bytes_copied=copied_bytes + bytes_copied, total_bytes=total_bytes,
                    current_source=source["path"], current_file=current_file,
                )

            staged(files=0, bytes_copied=0, current_file="")
            copied += stage(self.config, source, staging, self.stop_event.is_set, progress=staged)
            copied_bytes += sum(item["size"] for item in source["inventory"].values())
        # Size the parser acquisition bound to this verified inventory, rather than
        # imposing a product file-size ceiling. Context and archive guards remain.
        largest = max(
            (item["size"] for source in sources for item in source["inventory"].values()), default=1
        )
        store = initialize(
            state,
            staging,
            {
                "max_file_bytes": max(100_000_000, largest),
                **self.config.transcription_options(),
                "knowledge_cache_directory": str(self.config.home / "knowledge-cache"),
            },
        )
        extraction_progress = {}

        def extracted(counts):
            extraction_progress.update(counts)
            progress("ingesting", **counts)

        try:
            ingest_isolated(state, self.stop_event.is_set, progress=extracted)
            snapshot = store.one("SELECT snapshot_id FROM current_snapshot WHERE singleton=1")["snapshot_id"]
            manifest = store.manifest(snapshot)
        finally:
            store.close()
        # A verified extraction snapshot can support explicitly consented lexical
        # research while the dense serving projection is still being prepared.
        preparation_progress = {
            **extraction_progress,
            "documents": len(manifest["documents"]),
            "partial_publication": {
                "snapshot_id": snapshot,
                "state": str(state),
                "sources": [{k: s[k] for k in ("id", "path", "kind", "inventory")} for s in sources],
            },
        }
        progress("preparing", **preparation_progress)
        last_index_report = (None, 0.0)

        def indexed(**counts):
            nonlocal last_index_report
            if self.stop_event.is_set():
                raise InterruptedError("Application is stopping; job will resume on restart")
            now = time.monotonic()
            stage = counts["indexing_stage"]
            completed = counts.get("indexing_completed", counts.get("indexed_passages"))
            total = counts.get("indexing_total", counts.get("total_passages"))
            if stage != last_index_report[0] or completed == total or now - last_index_report[1] >= 1:
                progress("preparing", **preparation_progress, **counts)
                last_index_report = (stage, now)

        prepared = prepare(
            state,
            self.config.models,
            self.config.database_config,
            snapshot=snapshot,
            device=self.config.device,
            output=generation / "hybrid.json",
            progress=indexed,
        )
        with PostgresWorksetContext(self.dsn) as ledger:
            verified, segments, links = ledger.load_snapshot(snapshot)
        gaps = sum(
            bool(d["warnings"]) or d["status"] != "ready" or d["empty"] for d in verified["documents"]
        ) + len(verified.get("inventory_errors", []))
        return {
            "snapshot_id": snapshot,
            "knowledge_signature": verified.get("knowledge", {}).get("signature"),
            # Scheduling tracks the parent request separately from the worker's
            # actual immutable result. A hot package replacement cannot cause
            # an old parent to request the same upgrade forever. Restart after
            # installing code/models; the new process queues at most one refresh.
            "knowledge_request_signature": knowledge_signature(),
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
        with _connect(self.dsn) as db, db.transaction():
            row = self._row(db, True)
            recovered = db.execute(
                "UPDATE public.docworm_jobs SET state='queued',phase='queued',error=NULL,finished_at=NULL "
                "WHERE workspace=%s AND state='interrupted' RETURNING revision",
                (self.id,),
            ).fetchall()
            if any(job["revision"] == row["revision"] for job in recovered):
                db.execute(
                    "UPDATE public.docworm_workspace SET state='updating',phase='queued',message=%s WHERE id=%s",
                    ("Resuming interrupted processing. Checking sources before retrying the snapshot.", self.id),
                )

        def watch():
            while not self.stop_event.is_set():
                self._scan_requested.clear()
                try:
                    self.reconcile()
                except Exception:
                    # Requests still fail closed on DB/scan errors; retry next poll.
                    pass
                self._scan_requested.wait(self.config.scan_interval_seconds)

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
        self._scan_requested.set()
        for thread in self.threads:
            thread.join(timeout=5)
        # Keep live thread references: start() cannot spawn a duplicate executor.
        self.threads = [t for t in self.threads if t.is_alive()]
        with self._runtime_lock:
            if self._runtime:
                self._runtime.close()
                self._runtime = None

    def _ready(self):
        # A known unavailable collection must not wait behind a large checksum
        # scan, especially when the caller holds the investigation-history lock.
        with _connect(self.dsn) as db:
            current = self._row(db)
        if (
            current["state"] not in ("ready", "ready_with_gaps")
            or current["published_revision"] != current["revision"]
        ):
            raise NotReady("Current source revision is not ready")
        if not self.reconcile():
            raise NotReady("Source revision changed during verification; retry after rebuilding")
        with _connect(self.dsn) as db:
            row = self._row(db)
        if row["state"] not in ("ready", "ready_with_gaps") or row["published_revision"] != row["revision"]:
            raise NotReady("Current source revision is not ready")
        blocked = restricted_hashes(self.config)
        if any(
            item["sha256"] in blocked
            for source in (row["publication"] or {}).get("sources", [])
            for item in source.get("inventory", {}).values()
        ):
            raise NotReady("Published evidence includes restricted content; rebuild is required")
        return row

    def investigation_snapshot(self, question, allow_partial=False):
        """Capture verified immutable inputs, independently of current-view gating.

        The caller retains this snapshot for the lifetime of a run. No subsequent
        source revision is silently substituted. Early research requires consent.
        """
        from evidencekg.hybrid.sources import ReadOnlyStore, load_sources

        self.reconcile()
        with _connect(self.dsn) as db:
            row = self._row(db)
            active = {s["id"] for s in self._sources(db) if s["enabled"]}
            current = row["published_revision"] == row["revision"] and row["state"] in (
                "ready",
                "ready_with_gaps",
            )
            if not current and not allow_partial:
                raise NotReady("Indexing is incomplete. Explicitly allow the available snapshot or wait.")
            publication = row["publication"]
            partial = db.execute(
                "SELECT progress FROM public.docworm_jobs WHERE workspace=%s AND revision=%s "
                "AND state='running' ORDER BY started_at DESC LIMIT 1",
                (self.id, row["revision"]),
            ).fetchone()
            if not current and partial and isinstance(partial["progress"], dict):
                publication = partial["progress"].get("partial_publication") or publication
            if not publication:
                raise NotReady("No consistent snapshot is available yet. Wait for extraction to finish.")
            if not {s["id"] for s in publication["sources"]}.issubset(active):
                raise NotReady("The previous snapshot includes removed sources. Wait for the new snapshot.")
        frozen = dict(row, publication=publication, snapshot_id=publication["snapshot_id"])
        state = Path(publication["state"])
        if not state.is_relative_to(self.config.home / "generations"):
            raise ValueError("Snapshot is outside the private generation store")
        if "config" in publication:
            result = self._with_runtime(frozen, lambda runtime: runtime.retrieve(question, limit=12))
            chosen = [s.get("segment_id", s.get("id")) for s in result.get("segments", [])]
            retrieval = "hybrid"
        store = ReadOnlyStore(state)
        try:
            manifest, segments, links = load_sources(store, publication["snapshot_id"])
            if "config" not in publication:
                words = set(question.casefold().split())
                chosen = sorted(segments, key=lambda k: (-sum(w in segments[k]["text"].casefold()
                                                                            for w in words), k))[:12]
                retrieval = "early_lexical"
            # Preserve typed contrary/qualified context instead of relying only on top ranks.
            initial = list(chosen)
            related = list(dict.fromkeys(sid for key in initial for sid in
                                        segments.get(key, {}).get("knowledge_related", [])
                                        if sid in segments and sid not in initial))
            chosen += related[:24]
            related_omitted = len(related[24:]) + sum(
                segments.get(key, {}).get("knowledge_related_omitted", 0) for key in initial)
            collection_gaps = [{"document_version_id": d["document_version_id"],
                                "path": d["path"], "status": d["status"], "warnings": d.get("warnings", [])}
                               for d in manifest["documents"]
                               if d["status"] != "ready" or d.get("warnings")]
            collection_documents = len(manifest["documents"])
            selected_docs = {segments[k]["document_version_id"] for k in chosen if k in segments}
            manifest = dict(manifest, documents=[d for d in manifest["documents"]
                                                if d["document_version_id"] in selected_docs])
            segments = {k: v for k, v in segments.items() if v["document_version_id"] in selected_docs}
            from evidencekg.source_readings import attach_reviews

            attach_reviews(self.config.home, segments)
            occurrences = store.rows(
                "SELECT o.* FROM occurrences o JOIN snapshot_occurrences so ON so.occurrence_id=o.id "
                "WHERE so.snapshot_id=? ORDER BY o.id",
                (publication["snapshot_id"],),
            )
            features = store.rows(
                "SELECT DISTINCT f.* FROM features f JOIN occurrences o ON o.feature_id=f.id "
                "JOIN snapshot_occurrences so ON so.occurrence_id=o.id WHERE so.snapshot_id=? ORDER BY f.id",
                (publication["snapshot_id"],),
            )
            occurrences = [o for o in occurrences if o["segment_id"] in segments]
            feature_ids = {o["feature_id"] for o in occurrences}
            features = [f for f in features if f["id"] in feature_ids]
            retained_nodes = set(segments) | selected_docs | feature_ids | {o["id"] for o in occurrences}
            links = [link for link in links if link["from_node"] in retained_nodes
                     and (link["to_node"] is None or link["to_node"] in retained_nodes)]
            originals = {}
            total_bytes = 0
            for doc in manifest["documents"]:
                version = store.one(
                    "SELECT original_blob_sha FROM document_versions WHERE id=?",
                    (doc["document_version_id"],),
                )
                if version["original_blob_sha"]:
                    size = store.one(
                        "SELECT byte_length FROM blobs WHERE sha256=?", (version["original_blob_sha"],)
                    )["byte_length"]
                    total_bytes += size
                    if total_bytes > 256 * 1024 * 1024:
                        raise ValueError(
                            "The selected evidence exceeds the 256 MiB investigation capture limit. "
                            "Select a smaller source collection; no evidence was silently omitted."
                        )
                    originals[doc["document_version_id"]] = store.get(version["original_blob_sha"])
        finally:
            store.close()
        manifest = self._paths(manifest, frozen)
        collection_gaps = self._paths(collection_gaps, frozen)
        forbidden_paths = restricted_paths(self.config)
        forbidden_hashes = restricted_hashes(self.config)
        if any(doc["path"] in forbidden_paths for doc in manifest["documents"] + collection_gaps) or any(
            sha(content) in forbidden_hashes for content in originals.values()
        ):
            raise NotReady("This snapshot contains restricted evidence. Rebuild with those sources excluded.")
        return {
            "snapshot_id": publication["snapshot_id"],
            "revision": row["revision"],
            "published_revision": row["published_revision"],
            "partial": not current,
            "retrieval": retrieval,
            "manifest": manifest,
            "segments": self._paths(segments, frozen),
            "links": links,
            "originals": originals,
            "occurrences": occurrences,
            "features": features,
            "selected_segments": [s for s in chosen if s in segments],
            "known_source_files": sum(s["file_count"] for s in self.sources()["items"] if s["enabled"]),
            "collection_documents": collection_documents,
            "collection_gaps": collection_gaps[:100],
            "collection_gaps_total": len(collection_gaps),
            "related_context_omitted": related_omitted,
            "capture_limitations": [
                "Only retrieved documents are retained for this run; the full collection was not copied.",
                "Retrieval ranks candidates; it is not an exhaustive relevance assessment."
            ],
        }

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
        from evidencekg.knowledge_storage import ShardedRows

        if isinstance(value, (list, ShardedRows)):
            return [self._paths(v, row) for v in value]
        if isinstance(value, dict):
            return {
                k: self._path(v, row)
                if k in ("path", "source_path") and isinstance(v, str)
                else self._paths(v, row)
                for k, v in value.items()
            }
        return value

    def documents(self, query="", offset=0, limit=50, issues=""):
        if issues not in ("", "all", "unsupported", "failed", "partial"):
            raise ValueError("Unknown processing issue filter")

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
                and (
                    not issues
                    or (issues == "all" and (d["status"] != "ready" or d["warnings"] or d["empty"]))
                    or d["status"] == issues
                )
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

    def knowledge(self, **filters):
        """Read the published generation under the same source fence as evidence."""
        from evidencekg.hybrid.sources import ReadOnlyStore
        from evidencekg.retrieval import API
        from evidencekg.source_readings import overlay_knowledge

        def perform(row):
            store = ReadOnlyStore(row["publication"]["state"])
            try:
                result = API(store).knowledge_query(row["snapshot_id"], **filters)
                return self._paths(overlay_knowledge(self.config.home, store, row["snapshot_id"], result), row)
            finally:
                store.close()

        return self.evidence(perform)

    def graph(self, limit=2000, *, detailed=False):
        from evidencekg.hybrid.sources import ReadOnlyStore

        def perform(row):
            manifest, segments, links = self._snapshot(row)
            nodes = {
                d["document_version_id"]: {
                    "id": d["document_version_id"],
                    "label": self._path(d["path"], row),
                    "kind": "document",
                    "document_id": d["document_version_id"],
                }
                for d in manifest["documents"]
            }
            structural = []
            if detailed:
                store = ReadOnlyStore(row["publication"]["state"])
                try:
                    occurrences = store.rows(
                        "SELECT o.* FROM occurrences o JOIN snapshot_occurrences so ON so.occurrence_id=o.id "
                        "WHERE so.snapshot_id=? ORDER BY o.id",
                        (row["snapshot_id"],),
                    )
                    features = store.rows(
                        "SELECT DISTINCT f.* FROM features f JOIN occurrences o ON o.feature_id=f.id "
                        "JOIN snapshot_occurrences so ON so.occurrence_id=o.id WHERE so.snapshot_id=? ORDER BY f.id",
                        (row["snapshot_id"],),
                    )
                    from evidencekg.knowledge import load as load_knowledge
                    from evidencekg.knowledge import project as project_knowledge

                    knowledge_nodes, knowledge_edges = project_knowledge(
                        self._paths(load_knowledge(store, row["snapshot_id"]), row), row["snapshot_id"]
                    )
                finally:
                    store.close()
                for segment in segments.values():
                    key = segment["id"]
                    nodes[key] = {
                        "id": key,
                        "label": segment["text"][:100],
                        "kind": "passage",
                        "document_id": segment["document_version_id"],
                    }
                    structural.append(
                        {
                            "id": "contains:" + key,
                            "source": segment["document_version_id"],
                            "target": key,
                            "type": "contains",
                        }
                    )
                for feature in features:
                    key = feature["id"]
                    nodes[key] = {
                        "id": key,
                        "label": feature["canonical_value"],
                        "kind": "feature",
                        "document_id": None,
                    }
                for occurrence in occurrences:
                    key = occurrence["id"]
                    nodes[key] = {
                        "id": key,
                        "label": occurrence["raw_value"],
                        "kind": "occurrence",
                        "document_id": segments.get(occurrence["segment_id"], {}).get("document_version_id"),
                    }
                    structural.extend(
                        [
                            {
                                "id": "occurrence:" + key,
                                "source": occurrence["segment_id"],
                                "target": key,
                                "type": "contains_occurrence",
                            },
                            {
                                "id": "feature:" + key,
                                "source": key,
                                "target": occurrence["feature_id"],
                                "type": "feature_membership",
                            },
                        ]
                    )
                nodes.update({node["id"]: node for node in knowledge_nodes})
                structural.extend(knowledge_edges)
                for link in links:
                    for endpoint in (link["from_node"], link["to_node"]):
                        if endpoint and endpoint not in nodes:
                            nodes[endpoint] = {
                                "id": endpoint,
                                "label": endpoint,
                                "kind": "relationship_endpoint",
                                "document_id": None,
                            }
                    if not link["to_node"]:
                        key = "unresolved:" + link["id"]
                        nodes[key] = {
                            "id": key,
                            "label": "Unresolved relationship",
                            "kind": "unresolved",
                            "document_id": None,
                        }
                        structural.append(
                            {
                                "id": link["id"],
                                "source": link["from_node"],
                                "target": key,
                                "type": link["relation_type"],
                            }
                        )
            # Unresolved relationships have no endpoint and are shown in document details.
            edges = structural + [
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
