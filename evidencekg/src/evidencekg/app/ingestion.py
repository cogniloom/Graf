"""Keep source-sized ingestion allocations out of the dashboard server process."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_PROGRESS_LIMIT = 4096


def _read_progress(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(_PROGRESS_LIMIT + 1)
    except FileNotFoundError:
        return None
    try:
        if len(raw) > _PROGRESS_LIMIT:
            raise ValueError("Oversized progress record")
        counts = json.loads(raw)
        for key in (
            "processed_files",
            "total_files",
            "processed_documents",
            "failed_documents",
            "partial_documents",
            "unsupported_documents",
        ):
            if type(counts[key]) is not int or counts[key] < 0:
                raise ValueError("Invalid progress counter")
        if counts["processed_files"] > counts["total_files"]:
            raise ValueError("Invalid progress denominator")
        if counts["stage"] not in ("extracting", "finalizing"):
            raise ValueError("Invalid progress stage")
        return counts
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Ingestion progress reporting failed") from exc


def _diagnostic_record(path):
    """Best-effort bounded structured data, never raw parser output."""
    try:
        with path.open("rb") as stream:
            data = stream.read(_PROGRESS_LIMIT + 1)
        value = json.loads(data) if len(data) <= _PROGRESS_LIMIT else None
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _worker_failure(state, mailbox, code):
    from evidencekg.db import atomic

    context = _diagnostic_record(mailbox / "context.json")
    error = _diagnostic_record(mailbox / "error.json")
    if code == 2 and error.get("type") == "MemoryError":
        reason = "Memory allocation failed in the isolated worker (2 GB address-space limit)."
        action = "Retry with a smaller source selection. If it repeats, share the diagnostic report for a memory investigation."
    elif code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = str(-code)
        reason = f"The isolated worker was terminated by signal {name}."
        action = (
            "Check system logs for a memory kill or external termination before retrying; the signal alone does not establish the cause."
            if code == -signal.SIGKILL
            else "Review the last document in its usual viewer and share the diagnostic report if retrying fails again."
        )
    else:
        kind = error.get("type", "")
        kind = kind if isinstance(kind, str) and kind.isidentifier() and len(kind) <= 80 else "unknown error"
        reason = f"The isolated worker exited with code {code} ({kind})."
        action = "Review the last document in its usual viewer, then retry from Sources. If it repeats, share the diagnostic report."
    path = context.get("path")
    location = ""
    if isinstance(path, str):
        # JSON quoting prevents newlines/control characters from forging UI messages.
        location = f" Last document: {json.dumps(path[:300] + ('…' if len(path) > 300 else ''), ensure_ascii=False)} (not necessarily the cause)."
    stage = context.get("stage")
    if stage in ("initializing", "reading", "extracting", "finalizing"):
        location += f" Stage: {stage}."
    target = Path(state) / "ingestion-diagnostic.json"
    report = {"exit_code": code, "context": context, "error": error, "memory_limit_bytes": 2_000_000_000}
    try:
        atomic(target, json.dumps(report).encode())
        retained = f" Diagnostic report: {target}."
    except OSError:
        retained = (
            " The diagnostic report could not be saved; check free disk space and workspace permissions."
        )
    return ValueError(reason + location + " " + action + retained + " Your originals are unchanged.")


def ingest_isolated(state, stopped=lambda: False, progress=None):
    if stopped():
        raise InterruptedError("Source ingestion stopped")
    # Output is file-backed so diagnostics cannot grow the server's memory either.
    # A private latest-value mailbox bounds disk and memory regardless of corpus size.
    # Keep the initial record separately so even a fast worker reports real zero counts.
    with (
        tempfile.TemporaryDirectory(prefix="graf-ingest-progress-") as directory,
        tempfile.TemporaryFile() as output,
    ):
        mailbox = Path(directory)
        last = None
        last_sent = 0.0

        def poll_progress(force=False):
            nonlocal last, last_sent
            initial = _read_progress(mailbox / "initial.json") if last is None else None
            latest = _read_progress(mailbox / "latest.json")
            for counts in (initial, latest):
                if counts is None or counts == last:
                    continue
                now = time.monotonic()
                if (
                    not force
                    and last is not None
                    and counts["stage"] == last["stage"]
                    and now - last_sent < 1
                ):
                    continue
                if progress is not None:
                    progress(counts)
                last, last_sent = counts, now

        process = subprocess.Popen(
            [sys.executable, "-m", "evidencekg.app.ingestion", str(state), directory],
            stdout=output,
            stderr=output,
            # BLAS otherwise reserves stacks for every host CPU before the
            # optional local EN/DE parsers load, exhausting this worker's 2 GB
            # address-space guard despite modest resident memory. Keep the
            # existing guard and bound numerical threads in this worker only.
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "BLIS_NUM_THREADS": "1",
            },
            start_new_session=True,
        )
        try:
            while True:
                if stopped():
                    raise InterruptedError("Source ingestion stopped")
                poll_progress()
                try:
                    code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if stopped():
                        raise InterruptedError("Source ingestion stopped")
            if code:
                raise _worker_failure(state, mailbox, code)
            poll_progress(force=True)
            if (
                last is None
                or last["stage"] != "finalizing"
                or last["processed_files"] != last["total_files"]
            ):
                raise RuntimeError("Ingestion progress reporting failed: final acquisition counters missing")
        finally:
            if process.poll() is None:
                # Let the worker unwind parser cleanup before forcing group termination.
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            # Also terminate any descendant still in the ingestion process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def main():
    import resource

    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    os.environ["GRAF_INGEST_PROCESS_GROUP"] = str(os.getpgrp())
    # This is a worker memory guard, not a source-file acquisition ceiling.
    resource.setrlimit(resource.RLIMIT_AS, (2_000_000_000, 2_000_000_000))
    from evidencekg.db import Store, atomic
    from evidencekg.ingest import ingest

    mailbox = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    last_written = 0.0
    first = True

    def report(counts):
        nonlocal last_written, first
        now = time.monotonic()
        if not first and counts["stage"] == "extracting" and now - last_written < 0.1:
            return
        try:
            payload = json.dumps(counts).encode()
            if len(payload) > _PROGRESS_LIMIT:
                raise ValueError("Oversized progress record")
            if first:
                atomic(mailbox / "initial.json", payload)
            atomic(mailbox / "latest.json", payload)
        except Exception as exc:
            raise RuntimeError("Ingestion progress reporting failed") from exc
        first, last_written = False, now

    def diagnostic(context):
        if mailbox is not None:
            context = dict(context)
            if "path" in context:
                context["path"] = context["path"][:400]
            atomic(mailbox / "context.json", json.dumps(context).encode())

    store = None
    emergency_space = None
    try:
        emergency_space = bytearray(65_536)
        diagnostic({"stage": "initializing"})
        store = Store(Path(sys.argv[1]))
        ingest(store, progress=report if mailbox is not None else None, diagnostic=diagnostic)
    except Exception as exc:
        del emergency_space  # Leave room to report allocation failures.
        # Retain exception type and code locations, without exception messages,
        # source lines, locals, or parser stderr that may contain source content.
        frames = []
        tb = exc.__traceback__
        while tb is not None:
            frames.append(
                {
                    "file": Path(tb.tb_frame.f_code.co_filename).name,
                    "function": tb.tb_frame.f_code.co_name,
                    "line": tb.tb_lineno,
                }
            )
            tb = tb.tb_next
        if mailbox is not None:
            try:
                atomic(
                    mailbox / "error.json",
                    json.dumps({"type": type(exc).__name__, "frames": frames[-8:]}).encode(),
                )
            except (OSError, MemoryError):
                pass
        return 2 if isinstance(exc, MemoryError) else 1
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
