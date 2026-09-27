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
    return AppConfig(home=private, database_config=private / "db.json", models=private / "models",
                     token_file=private / "token", ui_dist=tmp_path / "ui")


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
        capture_output=True, text=True, timeout=40,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: normal cancellation, stopped-worker fallback, and abrupt worker death" in result.stdout
