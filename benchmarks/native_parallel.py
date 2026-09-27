"""Bounded parallel scheduling of the unchanged native benchmark protocol."""

import argparse
import csv
import fcntl
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from benchmarks import native
from benchmarks.native_finish import copy_changed


def selected_config(config, model, effort):
    return dict(config, schedule=[r for r in config["schedule"]
                                 if (r["model"], r["effort"]) == (model, effort)])


def worker(args):
    original_read = native.read
    config = original_read(args.output / "run.json")
    native.verify(args.output, config)
    selected = selected_config(config, args.model, args.effort)
    native.read = lambda path: selected if path == args.output / "run.json" else original_read(path)
    # Only the coordinator writes aggregate reports. Each pair owns its trial files.
    native.report = lambda output: None
    {"answer": native.run, "grade": native.grade}[args.stage](args.output, args.codex_home)


def publish(root, mirror, status, cohort):
    try:
        native.report(root)
    except ValueError as exc:
        if not status["blocked"]:
            raise
        status["report_error"] = str(exc)
    with (root / "per-case.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["execution_cohort"] = "sequential" if row["trial"] in cohort["prior_trials"] else "parallel"
        row["configured_concurrency"] = 1 if row["execution_cohort"] == "sequential" else cohort["concurrency"]
    with (root / "per-case-execution.csv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary = root / "pipeline-status.tmp"
    temporary.write_text(json.dumps(status, indent=2) + "\n")
    temporary.replace(root / "pipeline-status.json")
    shutil.copytree(root, mirror, dirs_exist_ok=True, copy_function=copy_changed)
    print(json.dumps(status), flush=True)


def coordinate(args):
    root = args.output
    lock = (root / "pipeline.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = native.read(root / "run.json")
    native.verify(root, config)
    cohort_path = root / "parallel-cohort.json"
    if not cohort_path.exists():
        native.write(cohort_path, dict(concurrency=args.jobs, started_unix=time.time(),
            prior_trials=[r["trial"] for r in config["schedule"] if (root / r["trial"] / "receipt.json").exists()],
            scheduler_sha=native.sha(Path(__file__)),
            timing_note="Per-case wall time includes parallel host/provider contention; cohorts are labeled."))
    cohort = native.read(cohort_path)
    if cohort["scheduler_sha"] != native.sha(Path(__file__)) or cohort["concurrency"] != args.jobs:
        raise ValueError("Parallel scheduler or concurrency changed")
    homes = []
    for i in range(args.jobs):
        home = args.codex_home.parent / f"{args.codex_home.name}-parallel-{i}"
        home.mkdir(exist_ok=True)
        auth = home / "auth.json"
        if not auth.exists():
            auth.symlink_to(args.codex_home / "auth.json")
        homes.append(home)
    combinations = list(dict.fromkeys((r["model"], r["effort"]) for r in config["schedule"]))
    blocked = []
    for stage in ("answer", "grade"):
        halted = native.halted_configurations(root, config)
        queue = [pair for pair in combinations if stage == "grade" or pair not in halted]
        active = {}
        while queue or active:
            for slot, (process, log, pair) in list(active.items()):
                code = process.poll()
                if code is not None:
                    log.close()
                    del active[slot]
                    if code != 0:
                        blocked.append(dict(stage=stage, model=pair[0], effort=pair[1], exit_code=code))
                        queue.clear()  # Drain already-running cases without interrupting usage receipts.
            for slot in range(args.jobs):
                if not queue or blocked:
                    break
                if slot in active:
                    continue
                pair = queue.pop(0)
                stem = f"parallel-{stage}-{pair[0]}-{pair[1]}-{time.time_ns()}"
                argv = [sys.executable, "-m", "benchmarks.native_parallel", "worker", str(root),
                        "--codex-home", str(homes[slot]), "--model", pair[0], "--effort", pair[1], "--stage", stage]
                native.write(root / f"{stem}.json", dict(argv=argv, slot=slot, started_unix=time.time()))
                log = (root / f"{stem}.log").open("x")
                active[slot] = (subprocess.Popen(argv, stdout=log, stderr=log), log, pair)
            try:
                receipts = [native.read(root / r["trial"] / "receipt.json") for r in config["schedule"]
                            if (root / r["trial"] / "receipt.json").exists()]
                try:
                    halted = native.halted_configurations(root, config)
                except ValueError as exc:
                    if not any(b.get("error") == str(exc) for b in blocked):
                        blocked.append(dict(stage=stage, error=str(exc)))
                    queue.clear()
                status = dict(phase=(stage + "ing") if queue or active else (stage + "_finished"),
                    scheduled=len(config["schedule"]), answered=sum(r["status"] == "answered" for r in receipts),
                    failed=sum(r["status"] != "answered" for r in receipts), halted_configurations=len(halted),
                    graded=sum((root / r["trial"] / "score.json").exists() for r in config["schedule"]),
                    configured_concurrency=args.jobs, active=len(active), queued_configurations=len(queue),
                    blocked=blocked, updated_unix=time.time())
                publish(root, args.mirror, status, cohort)
            except json.JSONDecodeError:
                if not active:
                    raise
            if active:
                time.sleep(10)
        if blocked:
            status.update(phase=stage + "_blocked", blocked=blocked)
            publish(root, args.mirror, status, cohort)
            return
    status.update(phase="complete_with_halted_configurations" if halted else "complete")
    publish(root, args.mirror, status, cohort)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("coordinate", "worker"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--stage", choices=("answer", "grade"))
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.codex_home = args.codex_home.resolve()
    if args.jobs < 1:
        parser.error("jobs must be positive")
    {"coordinate": coordinate, "worker": worker}[args.action](args)


if __name__ == "__main__":
    main()
