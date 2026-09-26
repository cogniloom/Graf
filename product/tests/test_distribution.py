import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_rejects_symlink_ancestors(tmp_path):
    release = load("release", "scripts/build_release.py")
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "secret.txt").write_text("SYNTHETIC_PRIVATE_SENTINEL")
    (root / "docs").symlink_to(outside, target_is_directory=True)
    with patch.object(release, "ROOT", root), pytest.raises(ValueError, match="Symlink"):
        release.checked(root / "docs" / "secret.txt")


def test_interrupted_install_starts_database_before_migration(tmp_path):
    launcher = load("launcher", "product/launcher.py")
    (tmp_path / "app.json").write_text("{}")
    events = []
    with (
        patch.object(launcher.shutil, "which", return_value="/tool"),
        patch.object(launcher, "compose", side_effect=lambda *a: events.append("compose")),
        patch.object(launcher, "migrate", side_effect=lambda _: events.append("migrate")),
        patch.object(launcher, "start", side_effect=lambda _: events.append("start")),
    ):
        launcher.install(SimpleNamespace(), tmp_path)
    assert events == ["compose", "migrate", "start"]
    assert (tmp_path / "installed.json").exists()


def test_backup_rejects_top_level_symlink(tmp_path):
    backup = load("backup", "product/backup.py")
    home = tmp_path / "home"
    home.mkdir()
    private = tmp_path / "outside"
    private.mkdir()
    (private / "secret.txt").write_text("SYNTHETIC_PRIVATE_SENTINEL")
    (home / "generations").symlink_to(private, target_is_directory=True)
    target = tmp_path / "backup"
    with pytest.raises(ValueError, match="symlink"):
        backup.backup(home, target, lambda *a, **kw: kw["stdout"].write(b"dump"))
    assert (target / "INCOMPLETE").exists()
    assert not (target / "generations/secret.txt").exists()
