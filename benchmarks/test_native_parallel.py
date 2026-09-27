"""Parallel isolation and bounded scheduler regression checks without model calls."""

import json
from argparse import Namespace
from pathlib import Path

from benchmarks import native_parallel as parallel


def test_worker_filters_only_schedule_and_preserves_protocol(tmp_path, monkeypatch):
    config = dict(schedule=[dict(model=m, effort="low", trial=m) for m in ("a", "b")], protocol="frozen")
    (tmp_path / "run.json").write_text(json.dumps(config))
    seen = []
    monkeypatch.setattr(parallel.native, "verify", lambda root, value: seen.append(value))
    original_read = parallel.native.read
    original_report = parallel.native.report
    def run(root, home):
        selected = parallel.native.read(root / "run.json")
        assert selected["schedule"] == config["schedule"][:1]
        assert selected["protocol"] == "frozen"
        assert home == tmp_path / "isolated"
    monkeypatch.setattr(parallel.native, "run", run)
    try:
        parallel.worker(Namespace(output=tmp_path, codex_home=tmp_path / "isolated",
                                  model="a", effort="low", stage="answer"))
    finally:
        parallel.native.read = original_read
        parallel.native.report = original_report
    assert seen == [config]
    assert original_read(tmp_path / "run.json") == config


def test_scheduler_isolates_homes_and_runs_bounded_slots(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / "auth.json").write_text("fake-test-login")
    rows = [dict(model=f"m{i}", effort="low", trial=f"t{i}") for i in range(8)]
    (root / "run.json").write_text(json.dumps(dict(schedule=rows)))
    monkeypatch.setattr(parallel.native, "verify", lambda *args: None)
    monkeypatch.setattr(parallel.native, "halted_configurations", lambda *args: {})
    monkeypatch.setattr(parallel.time, "sleep", lambda seconds: None)
    snapshots = []
    launches = []
    monkeypatch.setattr(parallel, "publish", lambda root, mirror, status, cohort: snapshots.append(dict(status)))
    class Process:
        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.polls = 0
            launches.append(argv)
        def poll(self):
            self.polls += 1
            if self.polls == 1:
                return None
            model = self.argv[self.argv.index("--model") + 1]
            stage = self.argv[self.argv.index("--stage") + 1]
            folder = root / next(r["trial"] for r in rows if r["model"] == model)
            folder.mkdir(exist_ok=True)
            if stage == "answer":
                (folder / "receipt.json").write_text('{"status":"answered"}')
            else:
                (folder / "score.json").write_text('{"passed":true}')
            return 0
    monkeypatch.setattr(parallel.subprocess, "Popen", Process)
    parallel.coordinate(Namespace(output=root, mirror=tmp_path / "mirror", codex_home=home, jobs=3))
    assert max(s["active"] for s in snapshots) == 3
    assert snapshots[-1]["phase"] == "complete"
    assert len(launches) == 16
    homes = {argv[argv.index("--codex-home") + 1] for argv in launches}
    assert len(homes) == 3
    assert all((Path(h) / "auth.json").is_symlink() for h in homes)
    assert len({(a[a.index("--model")+1], a[a.index("--stage")+1]) for a in launches}) == 16
