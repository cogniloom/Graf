#!/usr/bin/env python3
"""Allowlisted publication bundle; never copies workspace evidence or credentials."""

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "README.md",
    "LICENSE",
    "THIRD_PARTY.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "graf",
    ".gitignore",
    "evidencekg/pyproject.toml",
    "evidencekg/uv.lock",
    "product/launcher.py",
    "product/backup.py",
    "product/ui/package.json",
    "product/ui/package-lock.json",
    "product/ui/index.html",
    "product/ui/tsconfig.json",
    "product/ui/vite.config.ts",
    "product/ui/svelte.config.js",
    "product/ui/.prettierrc.json",
    "deploy/postgres/entrypoint.sh",
    "deploy/postgres/init.sh",
]
TREES = [
    "docs",
    "examples",
    "plugins/graf",
    "scripts",
    "evidencekg/src",
    "evidencekg/tests",
    "product/tests",
    "product/ui/tests",
    "product/ui/src",
    "product/ui/public",
    "product/ui/dist",
    ".github/workflows",
]
SKIP = {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv", ".git"}


def checked(path):
    relative = path.relative_to(ROOT)
    current = ROOT
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("Symlink publication input: " + str(current))
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Publication input escaped repository")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="Require vX.Y.Z or vX.Y.Zrc matching the package version")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "evidencekg/pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Release version must be X.Y.Z")
    if args.tag is not None and not re.fullmatch(rf"v{re.escape(version)}(?:rc)?", args.tag):
        raise ValueError(f"Release tag must be v{version} or v{version}rc, got {args.tag}")
    archive_version = args.tag[1:] if args.tag else version
    for metadata in ("product/ui/package.json", "plugins/graf/.codex-plugin/plugin.json"):
        if json.loads((ROOT / metadata).read_text())["version"] != version:
            raise ValueError(f"Version mismatch in {metadata}; expected {version}")
    if not (ROOT / "product/ui/dist/index.html").is_file():
        raise ValueError("Build the dashboard first")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / f"graf-{archive_version}"
    if stage.exists():
        raise ValueError("Release staging directory already exists; choose a fresh --output")
    stage.mkdir()
    paths = [ROOT / f for f in FILES]
    for tree in TREES:
        checked(ROOT / tree)
        paths.extend(
            p
            for p in (ROOT / tree).rglob("*")
            if p.is_file() and not any(part in SKIP for part in p.relative_to(ROOT).parts)
        )
    for path in sorted(set(paths)):
        checked(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing or symlink publication input: " + str(path))
        relative = path.relative_to(ROOT)
        if any(part.startswith(".evidencekg") for part in relative.parts):
            raise ValueError("Private publication input")
        dest = stage / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    # A clean public marketplace has its own namespace, separate from local personal catalogs.
    market = stage / ".agents/plugins/marketplace.json"
    market.parent.mkdir(parents=True)
    market.write_text(
        json.dumps(
            {
                "name": "graf",
                "interface": {"displayName": "Graf"},
                "plugins": [
                    {
                        "name": "graf",
                        "source": {"source": "local", "path": "./plugins/graf"},
                        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                        "category": "Productivity",
                    }
                ],
            },
            indent=2,
        )
        + "\n"
    )
    notices = stage / "licenses/frontend"
    notices.mkdir(parents=True)
    modules = checked(ROOT / "product/ui/node_modules")
    for package in modules.rglob("package.json"):
        if (
            "node_modules" in package.relative_to(modules).parts[:-1]
            and len(package.relative_to(modules).parts) > 3
        ):
            continue
        checked(package)
        try:
            meta = json.loads(package.read_text())
        except (ValueError, OSError):
            continue
        name = meta.get("name", "unknown").replace("/", "__")
        for notice in package.parent.iterdir():
            if notice.is_file() and notice.name.lower().startswith(
                ("license", "licence", "copying", "notice")
            ):
                checked(notice)
                if Path(name).name != name or name in (".", ".."):
                    raise ValueError("Invalid dependency name")
                destination = notices / name / notice.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(notice, destination)
    if not any("cosmograph" in str(p) for p in notices.rglob("*")):
        raise ValueError("Cosmograph license notice missing")
    manifest = {
        str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(stage.rglob("*"))
        if p.is_file()
    }
    (stage / "SHA256SUMS.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive = output / f"graf-{archive_version}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(stage, arcname=stage.name)
    (output / f"{archive.name}.sha256").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
    )
    print(json.dumps({"staging": str(stage), "archive": str(archive), "files": len(manifest)}, indent=2))


if __name__ == "__main__":
    main()
