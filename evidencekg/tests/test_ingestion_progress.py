"""Progress contracts exercised against real extraction and isolated workers."""

import json
import subprocess
from email.message import EmailMessage
from pathlib import Path

import pytest

from evidencekg import relationships
from evidencekg.app import ingestion
from evidencekg.config import initialize
from evidencekg.ingest import ingest


def test_real_ingest_counts_descendants_gaps_and_finalization(tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    root.mkdir()
    message = EmailMessage()
    message.set_content("Container body")
    message.add_attachment(b"Attachment text", maintype="text", subtype="plain", filename="child.txt")
    message.add_attachment(b"\x00binary", maintype="application", subtype="octet-stream", filename="child.xyz")
    (root / "a.eml").write_bytes(message.as_bytes())
    (root / "b.pdf").write_bytes(b"broken pdf")
    (root / "c.xyz").write_bytes(b"\x00unsupported")
    (root / "d.txt").write_bytes(b"invalid utf8 \xff")
    store = initialize(tmp_path / "vault", root)
    events = []
    original = relationships.resolve

    def resolve(*args):
        assert events[-1]["stage"] == "finalizing"
        assert events[-1]["extraction_complete"] is True
        assert not store.db.execute("SELECT * FROM current_snapshot").fetchone()
        return original(*args)

    monkeypatch.setattr(relationships, "resolve", resolve)
    try:
        snapshot = ingest(store, progress=events.append)
        docs = store.manifest(snapshot)["documents"]
        assert events[0]["processed_files"] == events[0]["processed_documents"] == 0
        assert events[0]["total_files"] == 4
        assert events[1]["processed_files"] == 1
        assert events[1]["processed_documents"] == 3
        assert events[-2]["stage"] == "extracting"
        assert events[-2]["processed_files"] == 4
        assert events[-1]["processed_documents"] == len(docs) == 6
        for status in ("failed", "partial", "unsupported"):
            expected = sum(d["status"] == status for d in docs)
            assert expected > 0
            assert events[-1][status + "_documents"] == expected
        monkeypatch.setattr(relationships, "resolve", original)
        # Failed extraction retries intentionally change extraction identities.
        again = []
        ingest(store, progress=again.append)
        assert again[-1] == events[-1]
    finally:
        store.close()


def test_callback_failure_aborts_snapshot_transaction(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "a.txt").write_text("Original")
    store = initialize(tmp_path / "vault", root)
    try:
        previous = ingest(store)
        (root / "a.txt").write_text("Replacement")

        def fail(counts):
            if counts["stage"] == "finalizing":
                raise RuntimeError("Progress consumer unavailable")

        with pytest.raises(RuntimeError, match="Progress consumer unavailable"):
            ingest(store, progress=fail)
        assert store.one("SELECT snapshot_id FROM current_snapshot")["snapshot_id"] == previous
        assert len(store.db.execute("SELECT * FROM snapshots").fetchall()) == 1
    finally:
        store.close()


@pytest.mark.parametrize("empty", [False, True])
def test_real_isolated_initial_final_and_private_mailbox_cleanup(tmp_path, monkeypatch, empty):
    root = tmp_path / "inputs"
    root.mkdir()
    if not empty:
        (root / "unsupported.xyz").write_bytes(b"\x00Unknown")
    store = initialize(tmp_path / "vault", root)
    events, mailboxes = [], []
    popen = subprocess.Popen

    def launch(command, **kwargs):
        mailbox = Path(command[-1])
        assert mailbox.stat().st_mode & 0o777 == 0o700
        mailboxes.append(mailbox)
        return popen(command, **kwargs)

    monkeypatch.setattr(ingestion.subprocess, "Popen", launch)
    try:
        ingestion.ingest_isolated(store.state, progress=events.append)
        assert events[0]["stage"] == "extracting"
        assert events[0]["processed_documents"] == 0
        assert events[0]["total_files"] == int(not empty)
        assert events[-1]["stage"] == "finalizing"
        assert events[-1]["processed_files"] == int(not empty)
        assert events[-1]["unsupported_documents"] == int(not empty)
        assert all(not path.exists() for path in mailboxes)
        assert store.one("SELECT snapshot_id FROM current_snapshot")
    finally:
        store.close()


def test_real_isolated_callback_failure_cancels_and_cleans(tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "a.txt").write_text("Source")
    store = initialize(tmp_path / "vault", root)
    processes, mailboxes = [], []
    popen = subprocess.Popen

    def launch(command, **kwargs):
        mailboxes.append(Path(command[-1]))
        process = popen(command, **kwargs)
        processes.append(process)
        return process

    def fail(counts):
        raise RuntimeError("Progress database unavailable")

    monkeypatch.setattr(ingestion.subprocess, "Popen", launch)
    try:
        with pytest.raises(RuntimeError, match="Progress database unavailable"):
            ingestion.ingest_isolated(store.state, progress=fail)
        assert all(process.poll() is not None for process in processes)
        assert all(not path.exists() for path in mailboxes)
    finally:
        store.close()


@pytest.mark.parametrize("payload", [b"broken", b"x" * 4097, json.dumps({"stage": "done"}).encode()])
def test_invalid_ipc_is_explicit_failure(tmp_path, payload):
    path = tmp_path / "latest.json"
    path.write_bytes(payload)
    with pytest.raises(RuntimeError, match="progress reporting failed"):
        ingestion._read_progress(path)


def test_reporting_preserves_snapshot_hash_for_successful_cached_ingest(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "a.txt").write_text("Stable source provenance")
    store = initialize(tmp_path / "vault", root)
    try:
        snapshot = ingest(store)
        events = []
        assert ingest(store, progress=events.append) == snapshot
        assert events[-1]["processed_documents"] == 1
    finally:
        store.close()


def test_manager_real_ingestion_progress_separates_staging(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from evidencekg.app import manager as module
    from evidencekg.app.config import AppConfig
    from evidencekg.app.files import inventory

    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    config = AppConfig(
        home=home,
        database_config=home / "db.json",
        models=home / "models",
        token_file=home / "token",
        ui_dist=tmp_path / "ui",
    )
    root = tmp_path / "inputs"
    root.mkdir()
    (root / "a.xyz").write_bytes(b"\x00Unsupported")
    source = {"id": "s1", "path": str(root), "kind": "directory"}
    source["inventory"] = inventory(config, source)
    source["file_count"] = len(source["inventory"])
    manager = SimpleNamespace(config=config, stop_event=threading.Event())
    events = []

    class PreparingReached(Exception):
        pass

    def prepare(*args, **kwargs):
        raise PreparingReached()

    monkeypatch.setattr(module, "prepare", prepare)
    with pytest.raises(PreparingReached):
        module.Manager._build(
            manager, {"id": "job"}, [source], lambda phase, **counts: events.append((phase, counts))
        )
    extracted = [counts for phase, counts in events if phase == "ingesting"]
    assert events[0] == ("staging", {"files": 1, "total_files": 1})
    assert extracted[0]["processed_files"] == 0
    assert extracted[-1]["processed_files"] == extracted[-1]["total_files"] == 1
    assert extracted[-1]["unsupported_documents"] == 1
    assert extracted[-1]["stage"] == "finalizing"
    assert all("files" not in counts for counts in extracted)
    assert events[-1][0] == "preparing"
    assert events[-1][1]["processed_files"] == 1
    assert events[-1][1]["total_files"] == 1
    assert events[-1][1]["unsupported_documents"] == 1


def test_real_isolated_streams_throttled_intermediate_counts(tmp_path):
    import time

    root = tmp_path / "inputs"
    root.mkdir()
    message = EmailMessage()
    message.set_content("A real MIME document parsed by its own subprocess")
    for number in range(80):
        (root / f"{number:03}.eml").write_bytes(message.as_bytes())
    store = initialize(tmp_path / "vault", root)
    events = []
    try:
        ingestion.ingest_isolated(
            store.state, progress=lambda counts: events.append((time.monotonic(), counts))
        )
        extracting = [(when, counts) for when, counts in events if counts["stage"] == "extracting"]
        assert len(extracting) >= 2, "Expected real progress before the worker completes"
        assert all(right[0] - left[0] >= 1 for left, right in zip(extracting, extracting[1:]))
        completed = [counts["processed_files"] for _, counts in events]
        assert completed == sorted(completed)
        assert any(0 < value < 80 for value in completed)
        assert events[-1][1]["stage"] == "finalizing"
        assert events[-1][1]["processed_files"] == 80
    finally:
        store.close()
