"""Synthetic Linux repro; run with the repository evidencekg virtualenv Python.

Creates only temporary fixtures and launches the real ingestion/parser workers.
No database services, models, external inference, or private sources are used.
A subreaper lets this harness reap its own adopted parser after force-killing
its parent, rather than leaving a zombie for the container init process.
"""
import ctypes
import os
import signal
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from reportlab.pdfgen.canvas import Canvas

from evidencekg.app.ingestion import ingest_isolated
from evidencekg.config import initialize

assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
REAL_POPEN = subprocess.Popen


def process_state(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0]
    except FileNotFoundError:
        return None


def reap_adopted():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if not pid:
                return
        except ChildProcessError:
            return


with TemporaryDirectory(prefix="graf-review-cancel-") as directory:
    root = Path(directory)
    inputs = root / "inputs"
    inputs.mkdir()
    pdf = Canvas(str(inputs / "synthetic.pdf"))
    for number in range(1500):
        pdf.drawString(30, 700, f"Synthetic review page {number}")
        pdf.showPage()
    pdf.save()

    for mode in ("normal", "sigstop", "sigkill"):
        store = initialize(root / mode, inputs)
        processes, parser_ids, observations = [], [], []
        started = time.monotonic()

        def record(*args, **kwargs):
            process = REAL_POPEN(*args, **kwargs)
            processes.append(process)
            return process

        def stopped():
            if not processes:
                return False
            process = processes[0]
            try:
                children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
            except FileNotFoundError:
                return False
            for child in children:
                try:
                    cmd = Path(f"/proc/{child}/cmdline").read_bytes()
                except FileNotFoundError:
                    continue
                if b"evidencekg.parsers.worker" not in cmd:
                    continue
                pid = int(child)
                parser_ids.append(pid)
                observations.append((process.pid, pid, os.getpgid(process.pid), os.getpgid(pid)))
                if mode == "sigstop":
                    os.kill(pid, signal.SIGSTOP)
                    os.kill(process.pid, signal.SIGSTOP)
                elif mode == "sigkill":
                    os.kill(pid, signal.SIGSTOP)
                    os.kill(process.pid, signal.SIGKILL)
                return True
            if time.monotonic() - started > 15:
                raise AssertionError("No live parser observed within 15 seconds")
            return False

        try:
            with patch("evidencekg.app.ingestion.subprocess.Popen", record):
                try:
                    ingest_isolated(store.state, stopped)
                except InterruptedError:
                    pass
                else:
                    raise AssertionError("Expected cancellation of active ingestion")
            assert observations, "No live parser observed: cancellation was not tested"
            for _ in range(100):
                reap_adopted()
                survivors = [pid for pid in parser_ids if process_state(pid) is not None]
                if not survivors:
                    break
                time.sleep(0.01)
            print(mode, "worker/parser/pgrps", observations, "survivors", survivors,
                  "elapsed", round(time.monotonic() - started, 2), flush=True)
            assert not survivors, f"Parser survived cancellation: {survivors}"
            assert all(worker_group == parser_group for _, _, worker_group, parser_group in observations)
        finally:
            for pid in parser_ids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            reap_adopted()
            store.close()
print("PASS: normal cancellation, stopped-worker fallback, and abrupt worker death")
