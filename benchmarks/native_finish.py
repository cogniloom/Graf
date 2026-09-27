"""Execute native answering then grading, continuously mirroring evidence."""

import argparse
import fcntl
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from benchmarks.native import halted_configurations


def load(path):
    for attempt in range(5):
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            if attempt == 4:
                raise
            time.sleep(1)


def copy_changed(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        before, after = source.stat(), destination.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return str(destination)
    return shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("mirror", type=Path)
    parser.add_argument("--codex-home", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    lock = (root / "pipeline.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = load(root / "run.json")
    rows = config["schedule"]
    answer_log = (root / f"execution-{time.time_ns()}.log").open("x")
    answer = subprocess.Popen([sys.executable, "-m", "benchmarks.native", "run", str(root),
                               "--codex-home", str(args.codex_home.resolve())],
                              stdout=answer_log, stderr=answer_log)
    judge = None
    log = None
    while True:
        # Observe exit before snapshotting receipts written by that process.
        answer_exit = answer.poll()
        judge_exit = judge.poll() if judge is not None else None
        receipts = [load(root / row["trial"] / "receipt.json") for row in rows
                    if (root / row["trial"] / "receipt.json").exists()]
        halted = halted_configurations(root, config)
        eligible = [r for r in rows if (r["model"], r["effort"]) not in halted]
        eligible_done = all((root / r["trial"] / "receipt.json").exists() for r in eligible)
        failed = sum(r["status"] != "answered" for r in receipts)
        graded = sum((root / row["trial"] / "score.json").exists() for row in rows)
        if answer_exit is None:
            phase = "answering"
        elif answer_exit != 0 or not eligible_done:
            phase = "answering_blocked"
        elif judge is None:
            log = (root / f"grading-{time.time_ns()}.log").open("x")
            judge = subprocess.Popen([sys.executable, "-m", "benchmarks.native", "grade", str(root),
                                      "--codex-home", str(args.codex_home.resolve())], stdout=log, stderr=log)
            phase = "grading"
        elif judge_exit is None:
            phase = "grading"
        else:
            phase = "complete" if judge_exit == 0 and graded == len(receipts) - failed else "grading_blocked"
        status = dict(phase=phase, scheduled=len(rows), answered=len(receipts) - failed,
                      failed=failed, halted_configurations=len(halted), eligible=len(eligible), graded=graded, answer_exit_code=answer_exit,
                      updated_unix=time.time())
        (root / "pipeline-status.json").write_text(json.dumps(status, indent=2) + "\n")
        shutil.copytree(root, args.mirror, dirs_exist_ok=True, copy_function=copy_changed)
        print(json.dumps(status), flush=True)
        if phase in {"complete", "answering_blocked", "grading_blocked"}:
            if log:
                log.close()
            answer_log.close()
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
