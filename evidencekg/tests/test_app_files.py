"""Real filesystem regressions; no database or model execution required."""

from dataclasses import replace
from pathlib import Path

import pytest

from evidencekg.app.config import AppConfig
from evidencekg.app.files import browse, inventory, opened, read_file, stage
from evidencekg.config import initialize
from evidencekg.ingest import ingest


@pytest.fixture
def config(tmp_path):
    private = tmp_path / "graf"
    private.mkdir()
    return AppConfig(
        home=private,
        database_config=private / "db.json",
        models=private / "models",
        token_file=private / "token",
        ui_dist=tmp_path / "ui",
    )


def test_arbitrary_location_and_legacy_configuration(config, tmp_path):
    source = tmp_path / "Dossier with spaces"
    source.mkdir()
    assert config.source(source) == source
    legacy = replace(config, allowed_roots=(tmp_path / "old-missing-root",))
    assert legacy.source(source) == source
    with pytest.raises(ValueError, match="workspace"):
        config.source(config.home)
    with pytest.raises(ValueError, match="absolute"):
        config.source("relative")


def test_browse_pagination_unicode_private_and_symlinks(config, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "Dossier Scheidung ü"
    folder.mkdir()
    (tmp_path / "alias").symlink_to(folder)
    for index in range(205):
        (tmp_path / f"file-{index:03}.txt").touch()
    result = browse(config)
    assert result["path"] == str(tmp_path)
    assert result["parent"] == str(tmp_path.parent)
    assert result["total"] == 206
    assert result["items"][0]["path"] == str(folder)
    assert len(browse(config, str(tmp_path), 200)["items"]) == 6
    assert browse(config, str(folder))["items"] == []
    with pytest.raises(ValueError, match="Symlink"):
        browse(config, str(tmp_path / "alias"))
    with pytest.raises(ValueError, match="Cannot open"):
        browse(config, str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="workspace"):
        browse(config, str(config.home))


def test_parent_inventory_excludes_private_workspace(config, tmp_path):
    (config.home / "secret").write_text("not source material")
    (tmp_path / "original.txt").write_text("original")
    source = {"id": "s1", "path": str(tmp_path), "kind": "directory"}
    assert set(inventory(config, source)) == {"original.txt"}


def test_large_file_streams_stages_and_reaches_ingestion(config, tmp_path):
    original = tmp_path / "large.bin"
    with original.open("wb") as stream:
        stream.truncate(100_000_001)
    source = {"id": "s1", "path": str(original), "kind": "file"}
    source["inventory"] = inventory(config, source)
    target = config.home / "staging"
    assert stage(config, source, target) == 1
    assert (target / "s1" / original.name).stat().st_size == 100_000_001
    store = initialize(config.home / "vault", target, {"max_file_bytes": 100_000_001})
    try:
        snapshot = ingest(store)
        document = store.manifest(snapshot)["documents"][0]
        assert document["blob"] == source["inventory"][original.name]["sha256"]
        assert not any("acquisition limit" in warning for warning in document["warnings"])
    finally:
        store.close()
    assert original.stat().st_size == 100_000_001
    with opened(original) as fd, pytest.raises(InterruptedError):
        read_file(fd, stopped=lambda: True)


def test_double_slash_cannot_bypass_private_exclusion(config, tmp_path):
    (config.home / "secret").write_text("private")
    alias = "/" + str(config.home)
    with pytest.raises(ValueError, match="workspace"):
        config.source(alias)
    with pytest.raises(ValueError, match="workspace"):
        browse(config, alias)
    source = {"path": "/" + str(tmp_path), "kind": "directory"}
    assert inventory(config, source) == {}


def test_import_bookkeeping_changes_do_not_invalidate_directory_inventory(config, tmp_path, monkeypatch):
    from evidencekg.app import files

    root = tmp_path / "corpus"
    metadata = root / "metadata"
    metadata.mkdir(parents=True)
    document = metadata / "evidence.json"
    document.write_text('{"approval": false}')
    (metadata / "server.log").write_text("ordinary evidence log")
    for name in files.IMPORT_RUNTIME_FILES:
        (metadata / name).write_text("old operational state")
    source = {"id": "s1", "path": str(root), "kind": "directory"}
    before = inventory(config, source)
    assert set(before) == {"metadata/evidence.json", "metadata/server.log"}
    original = files.read_file

    def update_bookkeeping(*args, **kwargs):
        result = original(*args, **kwargs)
        # Same atomic replacement used by the real import helper: it changes the
        # metadata directory's mtime, but not any included evidence.
        temporary = metadata / "graf-import-status.json.tmp"
        temporary.write_text("new operational state")
        temporary.replace(metadata / "graf-import-status.json")
        (metadata / "graf-import.log").write_text("still waiting")
        return result

    monkeypatch.setattr(files, "read_file", update_bookkeeping)
    assert inventory(config, source) == before
    monkeypatch.setattr(files, "read_file", original)
    single = {"id": "log", "path": str(metadata / "graf-import.log"), "kind": "file"}
    assert set(inventory(config, single)) == {"graf-import.log"}
    # A matching basename outside the reserved metadata location is evidence.
    (root / "graf-import.log").write_text("user log")
    assert "graf-import.log" in inventory(config, source)


def test_bookkeeping_temp_files_are_excluded_but_directories_and_symlinks_are_not(config, tmp_path):
    root = tmp_path / "corpus"
    metadata = root / "metadata"
    metadata.mkdir(parents=True)
    (metadata / "graf-import-status.json.tmp").write_text("partially written status")
    nested = metadata / "graf-import.log"
    nested.mkdir()
    (nested / "original.txt").write_text("must be retained")
    source = {"path": str(root), "kind": "directory"}
    assert set(inventory(config, source)) == {"metadata/graf-import.log/original.txt"}
    (metadata / "graf-import-status.json").symlink_to(nested / "original.txt")
    with pytest.raises(OSError):
        inventory(config, source)


def test_inventory_still_rejects_included_file_changes_during_scan(config, tmp_path, monkeypatch):
    from evidencekg.app import files

    root = tmp_path / "evidence"
    root.mkdir()
    document = root / "a.txt"
    document.write_text("before")
    original = files.read_file

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        document.write_text("after!")
        return result

    monkeypatch.setattr(files, "read_file", mutate)
    with pytest.raises(ValueError, match="changed during inventory"):
        inventory(config, {"path": str(root), "kind": "directory"})


def test_import_receipt_rename_between_scandir_and_stat_is_not_source_drift(config, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from evidencekg.app import files

    root = tmp_path / "corpus"
    metadata = root / "metadata"
    metadata.mkdir(parents=True)
    (root / "original.txt").write_text("evidence")
    temporary = metadata / "graf-import-status.json.tmp"
    temporary.write_text("updated status")
    original = files.os.scandir

    @contextmanager
    def racing(fd):
        with original(fd) as entries:
            result = []
            for entry in entries:
                if entry.name == temporary.name:

                    def vanished(**kwargs):
                        temporary.replace(metadata / "graf-import-status.json")
                        raise FileNotFoundError("receipt was atomically renamed")

                    result.append(SimpleNamespace(name=entry.name, stat=vanished))
                else:
                    result.append(entry)
            yield iter(result)

    monkeypatch.setattr(files.os, "scandir", racing)
    assert set(inventory(config, {"path": str(root), "kind": "directory"})) == {"original.txt"}


def test_staging_reports_verified_files_before_whole_source_finishes(config, tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(205):
        (root / f"{index:03}.txt").write_bytes(b"abc")
    source = {"id": "s1", "path": str(root), "kind": "directory"}
    source["inventory"] = inventory(config, source)
    reports = []
    stage(config, source, config.home / "stage", progress=lambda **p: reports.append(p))
    assert any(0 < p["files"] < 205 for p in reports)
    assert reports[-1] == {"files": 205, "bytes_copied": 615, "current_file": "204.txt"}


def test_staging_byte_progress_can_cancel_a_large_file(config, tmp_path, monkeypatch):
    from evidencekg.app import files

    original = tmp_path / "large.txt"
    original.write_bytes(b"x" * (3 * 1024 * 1024))
    source = {"id": "s1", "path": str(original), "kind": "file"}
    source["inventory"] = inventory(config, source)
    ticks = iter(range(100))
    monkeypatch.setattr(files.time, "monotonic", lambda: next(ticks))
    reports = []

    def superseded(**counts):
        reports.append(counts)
        raise InterruptedError("newer revision")

    with pytest.raises(InterruptedError, match="newer revision"):
        stage(config, source, config.home / "stage", progress=superseded)
    assert reports == [{"files": 0, "bytes_copied": 1024 * 1024, "current_file": "large.txt"}]
    assert original.stat().st_size == 3 * 1024 * 1024


def test_isolated_ingestion_accepts_large_source_and_reports_memory_failure(config, tmp_path):
    from evidencekg.app.ingestion import ingest_isolated

    source = tmp_path / "inputs"
    source.mkdir()
    large = source / "large.bin"
    with large.open("wb") as stream:
        stream.truncate(100_000_001)
    store = initialize(config.home / "isolated", source, {"max_file_bytes": 3_000_000_000})
    try:
        ingest_isolated(store.state)
        snapshot = store.one("SELECT snapshot_id FROM current_snapshot")["snapshot_id"]
        assert store.manifest(snapshot)["documents"][0]["blob"]
        # A sparse file larger than the worker address space fails in the child,
        # without making the test/server allocate that much memory.
        with large.open("wb") as stream:
            stream.truncate(3_000_000_000)
        with pytest.raises(ValueError, match="isolated worker"):
            ingest_isolated(store.state)
        assert store.one("SELECT snapshot_id FROM current_snapshot")["snapshot_id"] == snapshot
        with pytest.raises(InterruptedError):
            ingest_isolated(store.state, stopped=lambda: True)
    finally:
        store.close()


def test_active_and_forced_cancellation_reaps_parser_descendants():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_ingestion_cancellation.py"))],
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: normal cancellation, stopped-worker fallback, and abrupt worker death" in result.stdout
