"""Synthetic real-PostgreSQL lifecycle checks; never open an existing corpus.

EVIDENCEKG_APP_TEST_ADMIN_CONFIG names an administrative connection JSON. Tests
create/drop a UUID-named database and use the existing evidencekg runtime role
with grants limited to that disposable database. Model execution is deliberately
replaced by verified snapshot import; ingestion and registry are real.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from evidencekg.app import AppConfig, Manager, migrate
from evidencekg.app.files import inventory, stage
from evidencekg.app.manager import Missing, NotReady
from evidencekg.db import dump
from evidencekg.hybrid.postgres import PostgresWorkset, _connect, _lock_id
from evidencekg.hybrid.postgres import migrate as hybrid_migrate
from evidencekg.hybrid.runtime import database_dsn
from evidencekg.hybrid.sources import ReadOnlyStore, load_sources


@pytest.fixture(scope="session")
def app_database(tmp_path_factory):
    config_path = os.environ.get("EVIDENCEKG_APP_TEST_ADMIN_CONFIG")
    if not config_path:
        pytest.skip("Set EVIDENCEKG_APP_TEST_ADMIN_CONFIG to explicitly allow a disposable database")
    import psycopg
    from psycopg import sql

    original = json.loads(Path(config_path).read_text())
    admin = database_dsn(config_path)
    name = "graf_app_test_" + uuid4().hex
    directory = tmp_path_factory.mktemp("app-database")
    dbconfig = directory / "admin.json"
    dbconfig.write_text(json.dumps(original | {"dbname": name}))
    with psycopg.connect(admin, autocommit=True) as db:
        db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        admin_dsn = database_dsn(dbconfig)
        migrate(admin_dsn)
        hybrid_migrate(admin_dsn)
        # Do not change a shared role or existing database; only disposable tables.
        runtime_original = json.loads((Path(config_path).parent / "evidencekg.json").read_text())
        runtime_file = directory / "runtime.json"
        runtime_file.write_text(json.dumps(runtime_original | {"dbname": name}))
        with psycopg.connect(admin_dsn, autocommit=True) as db:
            for table in ("docworm_workspace", "docworm_sources", "docworm_jobs", "docworm_worksets"):
                db.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE ON public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(runtime_original["user"])
                    )
                )
            for table in (
                "hybrid_runs",
                "hybrid_candidates",
                "hybrid_reviews",
                "hybrid_snapshots",
                "hybrid_documents",
                "hybrid_passages",
                "hybrid_edges",
                "hybrid_results",
            ):
                db.execute(
                    sql.SQL("GRANT SELECT, INSERT ON public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(runtime_original["user"])
                    )
                )
        yield runtime_file, admin_dsn
    finally:
        with psycopg.connect(admin, autocommit=True) as db:
            db.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def imported_prepare(state, models, database_config, *, snapshot, device, output, progress=None):
    """Only replace local model execution; retain real verified immutable import."""
    store = ReadOnlyStore(state)
    ledger = PostgresWorkset(database_dsn(database_config))
    try:
        manifest, segments, links = load_sources(store, snapshot)
        ledger.import_snapshot(snapshot, store.snapshot(snapshot)["manifest_sha"], manifest, segments, links)
        Path(output).write_text(dump({"snapshot_id": snapshot, "database_config": str(database_config)}))
        return {"config": str(output)}
    finally:
        store.close()
        ledger.close()


class TestRuntime:
    __test__ = False

    def __init__(self, state, config):
        self.config = json.loads(Path(config).read_text())
        self.ledger = PostgresWorkset(database_dsn(self.config["database_config"]))

    def retrieve(self, question, limit=12):
        manifest, segments, _ = self.ledger.load_snapshot(self.config["snapshot_id"])
        rows = [s | {"segment_id": s["id"]} for s in segments.values()]
        key = self.ledger.freeze({"snapshot": self.config["snapshot_id"], "question": question}, rows, {})
        return {
            "segments": rows[:limit],
            "source_context": manifest["documents"],
            "workset_id": key,
            "backend": "test-verified-import",
            "generative_model_calls": 0,
        }

    def page(self, key, cursor=None, limit=100):
        return self.ledger.page(key, cursor, limit)

    def close(self):
        self.ledger.close()


@pytest.fixture
def manager(tmp_path, app_database, monkeypatch):
    source = tmp_path / "originals"
    source.mkdir()
    (source / "a.txt").write_text("Original evidence: the invoice was not approved.\n")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    token = home / "token"
    token.write_text("t" * 48)
    token.chmod(0o600)
    ui = home / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html>Real UI fixture</html>")
    config = AppConfig(
        home=home,
        database_config=app_database[0],
        models=tmp_path / "models",
        allowed_roots=(source,),
        token_file=token,
        ui_dist=ui,
        scan_interval_seconds=0.1,
    )
    monkeypatch.setattr("evidencekg.app.manager.prepare", imported_prepare)
    value = Manager(config, runtime_factory=TestRuntime)
    yield value
    value.stop()


def ready(manager):
    item = manager.register(str(manager.config.allowed_roots[0]))
    assert manager.run_once(), manager.jobs()
    assert manager.status()["state"] == "ready"
    return item


def test_real_ingest_originals_unchanged_and_current_views(manager):
    root = manager.config.allowed_roots[0]
    before = (root / "a.txt").read_bytes()
    ready(manager)
    status = manager.status()
    assert status["counts"] == dict(sources=1, documents=1, passages=1, connections=0, gaps=0)
    docs = manager.documents()
    assert docs["total"] == 1 and docs["items"][0]["path"] == str(root / "a.txt")
    doc = manager.document(docs["items"][0]["id"])
    assert doc["passages"][0]["text"] == before.decode()
    assert doc["next_offset"] is None
    assert (root / "a.txt").read_bytes() == before
    assert set(p.name for p in root.iterdir()) == {"a.txt"}
    result = manager.search("invoice")
    assert manager.workset(result["workset_id"])["total"] == 1
    graph = manager.graph(1)
    assert graph["total_nodes"] == 1 and len(graph["nodes"]) == 1


def test_removed_source_gates_all_views_and_cached_worksets(manager):
    source = ready(manager)
    old = manager.search("invoice")["workset_id"]
    doc = manager.documents()["items"][0]["id"]
    manager.change(source["id"], remove=True)
    for call in (
        manager.graph,
        manager.documents,
        lambda: manager.document(doc),
        lambda: manager.search("invoice"),
        lambda: manager.workset(old),
    ):
        with pytest.raises(NotReady):
            call()
    assert manager.status()["snapshot_id"] is None
    assert manager.run_once()
    assert manager.status()["state"] == "empty"
    assert (manager.config.allowed_roots[0] / "a.txt").is_file()
    ready(manager)
    with pytest.raises(Missing, match="current source revision"):
        manager.workset(old)


def test_hash_change_with_preserved_size_and_mtime_is_immediately_gated(manager):
    ready(manager)
    path = manager.config.allowed_roots[0] / "a.txt"
    original_stat = path.stat()
    path.write_text(path.read_text().replace("not", "now"))
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(NotReady):
        manager.documents()
    assert manager.run_once()
    key = manager.documents()["items"][0]["id"]
    assert "now approved" in manager.document(key)["passages"][0]["text"]


def test_paused_sources_excluded_then_resume_and_missing_files_block(manager):
    source = ready(manager)
    manager.change(source["id"], enabled=False)
    with pytest.raises(NotReady):
        manager.graph()
    assert manager.run_once()
    assert manager.status()["state"] == "empty"
    assert manager.sources()["items"][0]["status"] == "paused"
    manager.change(source["id"], enabled=True)
    assert manager.run_once()
    (manager.config.allowed_roots[0] / "a.txt").unlink()
    # Empty directory is a known denominator; deleting the root is an unknown one.
    manager.config.allowed_roots[0].rmdir()
    with pytest.raises(NotReady):
        manager.documents()
    assert not manager.run_once()
    assert manager.status()["state"] == "blocked"
    assert manager.sources()["items"][0]["error"]


def test_path_traversal_symlinks_overlapping_roots_refused(manager, tmp_path):
    root = manager.config.allowed_roots[0]
    for path in (str(root / ".." / "originals" / "a.txt"), str(tmp_path / "outside.txt")):
        with pytest.raises(ValueError):
            manager.register(path)
    link = root / "alias.txt"
    link.symlink_to(root / "a.txt")
    with pytest.raises(ValueError, match="Symlink"):
        manager.register(str(link))
    item = manager.register(str(root))
    with pytest.raises(ValueError, match="overlap"):
        manager.register(str(root / "a.txt"))
    assert not manager.run_once()
    assert manager.status()["state"] == "blocked"
    link.unlink()
    manager.change(item["id"], rescan=True)
    assert manager.run_once()


def test_staging_detects_drift_and_never_writes_original(manager, tmp_path):
    path = manager.config.allowed_roots[0] / "a.txt"
    source = manager.register(str(path))
    source["inventory"] = inventory(manager.config, source)
    path.write_text("Changed after inventory")
    with pytest.raises(ValueError, match="hash changed"):
        stage(manager.config, source, tmp_path / "stage")
    assert path.read_text() == "Changed after inventory"


def test_removed_during_build_cannot_publish_and_no_duplicate_executor(manager):
    source = manager.register(str(manager.config.allowed_roots[0]))
    entered, release = threading.Event(), threading.Event()
    original = manager.builder
    outcomes = []

    def delayed(job, sources, progress):
        result = original(job, sources, progress)
        entered.set()
        assert release.wait(10)
        return result

    manager.builder = delayed
    worker = threading.Thread(target=lambda: outcomes.append(manager.run_once()))
    worker.start()
    try:
        assert entered.wait(15)
        second = Manager(manager.config, runtime_factory=TestRuntime)
        assert not second.run_once()
        manager.change(source["id"], remove=True)
    finally:
        release.set()
        worker.join(15)
    assert outcomes == [False]
    assert manager.status()["published_revision"] is None
    assert manager.run_once()
    assert manager.status()["state"] == "empty"
    assert any(j["state"] == "superseded" for j in manager.jobs()["items"])


def test_recover_crashed_job_under_exclusive_election(manager):
    manager.register(str(manager.config.allowed_roots[0]))
    manager.reconcile()
    with _connect(manager.dsn) as db:
        db.execute(
            "UPDATE public.docworm_jobs SET state='running',attempts=1 WHERE workspace=%s AND state='queued'",
            (manager.id,),
        )
    assert manager.run_once()
    assert manager.jobs()["items"][0]["attempts"] == 2
    assert manager.jobs()["items"][0]["state"] == "succeeded"
    assert not manager.run_once()


def test_failure_does_not_publish_and_manual_retry_recovers(manager):
    ready(manager)
    old = manager.status()["published_revision"]
    manager.rebuild()
    builder = manager.builder
    manager.builder = lambda *args: (_ for _ in ()).throw(ValueError("Synthetic preparation failure"))
    assert not manager.run_once()
    assert manager.status()["state"] == "blocked"
    assert manager.status()["published_revision"] == old
    with pytest.raises(NotReady):
        manager.documents()
    assert not manager.run_once()  # no busy automatic retry of unchanged failures
    manager.builder = builder
    manager.rebuild()
    assert manager.run_once()


def test_read_started_before_removal_discards_its_result(manager):
    source = ready(manager)

    def racing_read(row):
        manager.change(source["id"], remove=True)
        return {"private": "must never leave the gate"}

    with pytest.raises(NotReady):
        manager.evidence(racing_read)


def test_runtime_role_has_no_ddl_delete_or_hybrid_update(manager):
    import psycopg

    with _connect(manager.dsn) as db:
        for statement in (
            "CREATE TABLE public.app_forbidden(id int)",
            "DELETE FROM public.docworm_workspace",
            "UPDATE public.hybrid_snapshots SET id=id",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                db.execute(statement)


def test_external_election_lock_prevents_claim(manager):
    manager.register(str(manager.config.allowed_roots[0]))
    with _connect(manager.dsn) as leader:
        leader.execute("SELECT pg_advisory_lock(%s)", (_lock_id("docworm-worker:" + manager.id),))
        assert not manager.run_once()
        assert manager.jobs()["items"][0]["state"] == "queued"
    assert manager.run_once()


def test_periodic_watcher_detects_changes_and_stop_ends_polling(manager):
    import time

    ready(manager)
    original_revision = manager.status()["revision"]
    manager.start()
    path = manager.config.allowed_roots[0] / "a.txt"
    path.write_text("Periodic watcher must detect this edit.")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = manager.status()  # does not invoke synchronous reconcile
        if status["revision"] > original_revision and status["state"] == "ready":
            break
        time.sleep(0.05)
    else:
        pytest.fail("Background watcher did not publish the changed source")
    manager.stop()
    assert manager.threads == []
    stopped_revision = manager.status()["revision"]
    path.write_text("No scans should run after stop.")
    time.sleep(0.2)
    assert manager.status()["revision"] == stopped_revision


def test_stop_during_build_never_publishes_and_restart_recovers(manager):
    manager.register(str(manager.config.allowed_roots[0]))
    original = manager.builder

    def stopped_build(job, sources, progress):
        result = original(job, sources, progress)
        manager.stop_event.set()
        return result

    manager.builder = stopped_build
    assert not manager.run_once()
    assert manager.status()["published_revision"] is None
    assert manager.jobs()["items"][0]["state"] == "interrupted"
    manager.builder = original
    manager.start()
    import time

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and manager.status()["state"] != "ready":
        time.sleep(0.05)
    assert manager.status()["state"] == "ready"
    manager.stop()


def test_stale_executor_cannot_overwrite_recovered_progress(manager):
    manager.register(str(manager.config.allowed_roots[0]))
    manager.reconcile()
    with _connect(manager.dsn) as db:
        job = db.execute(
            "UPDATE public.docworm_jobs SET state='running',attempts=2 "
            "WHERE workspace=%s AND state='queued' RETURNING *",
            (manager.id,),
        ).fetchone()
    old = job | {"attempts": 1}
    with pytest.raises(NotReady, match="replaced"):
        manager._progress(old, "preparing", documents=1000)
    assert manager.jobs()["items"][0]["progress"] == {}


def test_unsupported_file_is_an_explicit_gap(manager):
    path = manager.config.allowed_roots[0] / "unknown.xyz"
    path.write_bytes(b"\x00Not a supported document format")
    manager.register(str(manager.config.allowed_roots[0]))
    assert manager.run_once()
    assert manager.status()["state"] == "ready_with_gaps"
    assert manager.status()["counts"]["gaps"] == 1
    assert any(d["status"] == "unsupported" for d in manager.documents()["items"])


def test_pdf_larger_than_100mb_is_ingested(manager):
    from reportlab.pdfgen.canvas import Canvas

    path = manager.config.allowed_roots[0] / "ordinary.pdf"
    canvas = Canvas(str(path), pageCompression=0)
    canvas.drawString(40, 700, "Large PDF acquisition is independent of segment budgets.")
    canvas._code.append("%" + "padding" * 20000)
    canvas.save()
    # Preserve the real PDF objects/xref, then add inert padding and a valid
    # final startxref marker. Extraction must still return the original text.
    original = path.read_bytes()
    xref = original.rsplit(b"startxref", 1)[1].split()[0]
    with path.open("ab") as stream:
        stream.write(b"\n%")
        for _ in range(100):
            stream.write(b"x" * 1_000_000)
        stream.write(b"\nstartxref\n" + xref + b"\n%%EOF\n")
    assert path.stat().st_size > 100_000_000
    manager.register(str(path))
    assert manager.run_once(), manager.jobs()
    doc = manager.document(manager.documents()["items"][0]["id"])
    # Native PDF extraction deliberately reports visual/handwriting coverage gaps.
    assert doc["status"] == "partial"
    assert manager.status()["state"] == "ready_with_gaps"
    assert any("visual content" in warning for warning in doc["warnings"])
    assert "Large PDF acquisition" in doc["passages"][0]["text"]


def test_source_over_100mb_outside_legacy_roots_is_ingested(manager, tmp_path):
    path = tmp_path / "large source.bin"
    with path.open("wb") as stream:
        stream.truncate(100_000_001)
    manager.register(str(path))
    assert manager.run_once(), manager.jobs()
    source = manager.sources()["items"][0]
    assert source["file_count"] == 1
    assert source["error"] is None
    assert manager.status()["state"] == "ready_with_gaps"  # Unsupported binary, not an acquisition failure.
    assert path.stat().st_size == 100_000_001


def test_detailed_graph_includes_passages_before_any_investigation(manager):
    ready(manager)
    graph = manager.graph(detailed=True)
    assert any(node["kind"] == "document" for node in graph["nodes"])
    assert any(node["kind"] == "passage" for node in graph["nodes"])
    assert any(edge["type"] == "contains" for edge in graph["edges"])
    assert all(edge["source"] in {n["id"] for n in graph["nodes"]} for edge in graph["edges"])


def test_retry_progress_clears_interrupted_workspace_message(manager):
    manager.register(str(manager.config.allowed_roots[0]))
    original = manager.builder

    def interrupt(job, sources, progress):
        raise InterruptedError("Source ingestion stopped")

    manager.builder = interrupt
    assert not manager.run_once()
    assert manager.status()["state"] == "blocked"
    with _connect(manager.dsn) as db:
        db.execute("UPDATE public.docworm_jobs SET state='queued' WHERE workspace=%s AND state='interrupted'", (manager.id,))

    def retry(job, sources, progress):
        progress("ingesting", files=1)
        status = manager.status()
        assert status["state"] == "updating"
        assert status["phase"] == "ingesting"
        assert "stopped" not in status["message"]
        return original(job, sources, progress)

    manager.builder = retry
    assert manager.run_once()
