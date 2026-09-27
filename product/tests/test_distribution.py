import importlib.util
import json
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


@pytest.mark.parametrize("tag", ["v0.2.0", "0.1.0", "v0.1.0-rc1"])
def test_release_rejects_wrong_tag_before_staging(tmp_path, monkeypatch, tag):
    release = load("release", "scripts/build_release.py")
    (tmp_path / "evidencekg").mkdir()
    (tmp_path / "evidencekg/pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    monkeypatch.setattr(release, "ROOT", tmp_path)
    output = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["build_release.py", "--output", str(output), "--tag", tag])
    with pytest.raises(ValueError, match="Release tag must be"):
        release.main()
    assert not output.exists()


def test_release_rejects_inconsistent_component_version(tmp_path, monkeypatch):
    release = load("release", "scripts/build_release.py")
    (tmp_path / "evidencekg").mkdir()
    (tmp_path / "evidencekg/pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (tmp_path / "product/ui").mkdir(parents=True)
    (tmp_path / "product/ui/package.json").write_text(json.dumps({"version": "0.1.0"}))
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["build_release.py", "--tag", "v1.2.3"])
    with pytest.raises(ValueError, match="Version mismatch"):
        release.main()
    assert not (tmp_path / "dist").exists()


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


@pytest.mark.parametrize(
    "tag", ["v0.1.0-rc.0", "v0.1.0-rc.01", "v0.1.0-rc.-1", "v0.2.0rc", "v0.1.0rc/escape"]
)
def test_release_rejects_invalid_candidate_tag(tmp_path, monkeypatch, tag):
    test_release_rejects_wrong_tag_before_staging(tmp_path, monkeypatch, tag)


def test_candidate_archive_name_and_checksum(tmp_path, monkeypatch):
    import hashlib
    import tarfile

    release = load("release", "scripts/build_release.py")
    root = tmp_path / "repo"
    for directory in [
        "evidencekg",
        "product/ui/dist",
        "plugins/graf/.codex-plugin",
        "product/ui/node_modules/cosmograph",
    ]:
        (root / directory).mkdir(parents=True)
    (root / "evidencekg/pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    for metadata in ["product/ui/package.json", "plugins/graf/.codex-plugin/plugin.json"]:
        (root / metadata).write_text('{"version":"0.1.0"}')
    (root / "product/ui/dist/index.html").write_text("synthetic dashboard")
    (root / "product/ui/node_modules/cosmograph/package.json").write_text('{"name":"cosmograph"}')
    (root / "product/ui/node_modules/cosmograph/LICENSE").write_text("synthetic license fixture")
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "FILES", ["product/ui/dist/index.html"])
    monkeypatch.setattr(release, "TREES", [])
    output = tmp_path / "output"
    monkeypatch.setattr("sys.argv", ["build_release.py", "--output", str(output), "--tag", "v0.1.0rc"])
    release.main()
    archive = output / "graf-0.1.0rc.tar.gz"
    assert (output / (archive.name + ".sha256")).read_text() == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest() + "  " + archive.name + "\n"
    with tarfile.open(archive) as bundle:
        assert "graf-0.1.0rc/product/ui/dist/index.html" in bundle.getnames()


def test_set_release_version_synchronizes_all_metadata(tmp_path):
    import shutil
    import tomllib

    setter = load("set_release_version", "scripts/set_release_version.py")
    files = ["evidencekg/pyproject.toml", "evidencekg/uv.lock", "product/ui/package.json",
             "product/ui/package-lock.json", "plugins/graf/.codex-plugin/plugin.json"]
    for relative in files:
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, dest)
    setter.set_version(tmp_path, "0.2.0")
    assert tomllib.loads((tmp_path / files[0]).read_text())["project"]["version"] == "0.2.0"
    lock = tomllib.loads((tmp_path / files[1]).read_text())
    assert next(p for p in lock["package"] if p["name"] == "evidencekg")["version"] == "0.2.0"
    for relative in files[2:]:
        assert json.loads((tmp_path / relative).read_text())["version"] == "0.2.0"
    assert json.loads((tmp_path / files[3]).read_text())["packages"][""]["version"] == "0.2.0"
    before = {f: (tmp_path / f).read_bytes() for f in files}
    (tmp_path / files[4]).write_text('{"version":"9.0.0"}')
    with pytest.raises(ValueError, match="Version mismatch"):
        setter.set_version(tmp_path, "0.3.0")
    for relative in files[:-1]:
        assert (tmp_path / relative).read_bytes() == before[relative]


def test_backup_preserves_investigation_history_keys_and_restrictions(tmp_path):
    backup = load("backup", "product/backup.py")
    home = tmp_path / "home"
    vault = home / "investigations" / "vault"
    vault.mkdir(parents=True)
    (vault / "signing.key").write_bytes(b"synthetic signing key fixture")
    (home / "investigations" / "sessions.sqlite3").write_bytes(b"synthetic restriction fixture")
    target = tmp_path / "archive"
    backup.backup(home, target, lambda *a, **kw: kw["stdout"].write(b"dump"))
    manifest = json.loads((target / "manifest.json").read_text())
    for name in ("investigations/vault/signing.key", "investigations/sessions.sqlite3"):
        assert (target / name).read_bytes() == (home / name).read_bytes()
        assert manifest["files"][name] == backup.digest(target / name)
