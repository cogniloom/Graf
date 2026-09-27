"""Keep source-sized ingestion allocations out of the dashboard server process."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path


def ingest_isolated(state, stopped=lambda: False):
    if stopped():
        raise InterruptedError("Source ingestion stopped")
    # Output is file-backed so diagnostics cannot grow the server's memory either.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "evidencekg.app.ingestion", str(state)],
            stdout=output,
            stderr=output,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
            start_new_session=True,
        )
        try:
            while True:
                try:
                    code = process.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if stopped():
                        raise InterruptedError("Source ingestion stopped")
            if code:
                raise ValueError(
                    "Document processing could not finish in its isolated worker "
                    "(resource exhaustion or parser failure). Your originals are unchanged."
                )
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
    from evidencekg.db import Store
    from evidencekg.ingest import ingest

    store = Store(Path(sys.argv[1]))
    try:
        ingest(store)
    except MemoryError:
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
