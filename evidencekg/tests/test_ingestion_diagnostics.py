"""Exercise diagnostic retention with real isolated process failures."""

import json
import signal
import subprocess
import sys

import pytest
from test_app_lifecycle import app_database, manager

from evidencekg.app import ingestion
from evidencekg.config import initialize

__all__ = ["app_database", "manager"]


@pytest.mark.parametrize(
    "mode,expected,code",
    [
        ("exception", "RuntimeError", 1),
        ("memory", "Memory allocation failed", 2),
        ("signal", "SIGKILL", -signal.SIGKILL),
    ],
)
def test_real_worker_failure_retains_context_without_source_contents(
    tmp_path, monkeypatch, mode, expected, code
):
    root = tmp_path / "inputs"
    root.mkdir()
    document = root / "invoice.txt"
    document.write_text("CONFIDENTIAL DOCUMENT CONTENT")
    store = initialize(tmp_path / "vault", root)
    original = subprocess.Popen

    def launch(command, **kwargs):
        # Run the actual worker main, replacing only the parser with a known failure.
        failure = {
            "exception": "raise RuntimeError('CONFIDENTIAL DOCUMENT CONTENT')",
            "memory": "raise MemoryError()",
            "signal": "os.kill(os.getpid(), signal.SIGKILL)",
        }[mode]
        script = (
            "import os,signal,sys\n"
            "from evidencekg import parsers\n"
            "from evidencekg.app.ingestion import main\n"
            f"def fail(*args, **kwargs):\n    {failure}\n"
            "parsers.parse=fail\n"
            f"sys.argv={command[2:]!r}\n"
            "sys.exit(main())\n"
        )
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(ingestion.subprocess, "Popen", launch)
    try:
        with pytest.raises(ValueError, match=expected) as raised:
            ingestion.ingest_isolated(store.state)
        message = str(raised.value)
        assert "invoice.txt" in message
        assert "not necessarily the cause" in message
        assert "CONFIDENTIAL" not in message
        report_path = store.state / "ingestion-diagnostic.json"
        report = json.loads(report_path.read_text())
        assert report["exit_code"] == code
        assert report["context"] == {"stage": "extracting", "path": "invoice.txt"}
        assert "CONFIDENTIAL" not in report_path.read_text()
        assert report_path.stat().st_mode & 0o077 == 0
        assert not store.db.execute("SELECT * FROM current_snapshot").fetchone()
        assert document.read_text() == "CONFIDENTIAL DOCUMENT CONTENT"
        if mode == "signal":
            assert "does not establish the cause" in message
        else:
            assert report["error"]["frames"]
    finally:
        store.close()


def test_real_memory_limit_identifies_file_before_capture(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    document = root / "huge.bin"
    with document.open("wb") as stream:
        stream.truncate(3_000_000_000)
    store = initialize(tmp_path / "vault", root, {"max_file_bytes": 3_000_000_000})
    try:
        with pytest.raises(ValueError, match="Memory allocation failed") as raised:
            ingestion.ingest_isolated(store.state)
        assert "huge.bin" in str(raised.value)
        report = json.loads((store.state / "ingestion-diagnostic.json").read_text())
        assert report["context"]["stage"] == "reading"
    finally:
        store.close()


def test_missing_diagnostics_do_not_invent_cause(tmp_path):
    error = ingestion._worker_failure(tmp_path, tmp_path, 9)
    assert "code 9 (unknown error)" in str(error)
    assert "Last document" not in str(error)


def test_failure_is_visible_through_authenticated_status(tmp_path, manager):
    from fastapi.testclient import TestClient

    from evidencekg.app.api import create_app

    source = manager.config.allowed_roots[0]
    (source / "sample.txt").write_text("Original")
    manager.register(str(source))
    mailbox = tmp_path / "mailbox"
    mailbox.mkdir()
    (mailbox / "context.json").write_text(json.dumps({"stage": "extracting", "path": "sample.txt"}))
    (mailbox / "error.json").write_text(json.dumps({"type": "MemoryError"}))

    def fail(*args):
        raise ingestion._worker_failure(manager.config.home, mailbox, 2)

    manager.builder = fail
    manager.run_once()
    with TestClient(
        create_app(manager.config, manager=manager, start_background=False), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/status").status_code == 401
        status = client.get("/api/status", headers={"Authorization": "Bearer " + "t" * 48}).json()
        assert status["state"] == "blocked"
        assert status["jobs"][0]["state"] == "failed"
        assert "Memory allocation failed" in status["message"]
        assert "sample.txt" in status["jobs"][0]["error"]
        assert "smaller source selection" in status["message"]
        assert (source / "sample.txt").read_text() == "Original"
