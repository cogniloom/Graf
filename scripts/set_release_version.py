#!/usr/bin/env python3
"""Synchronize release metadata in a disposable CI checkout or local worktree."""

import argparse
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def set_version(root, version):
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Version must be X.Y.Z")
    project = root / "evidencekg/pyproject.toml"
    old = tomllib.loads(project.read_text())["project"]["version"]
    changes = {}
    for relative, section in [("evidencekg/pyproject.toml", "project"), ("evidencekg/uv.lock", "package")]:
        path = root / relative
        content = path.read_text()
        parsed = tomllib.loads(content)
        if section == "package":
            packages = [p for p in parsed["package"] if p["name"] == "evidencekg"]
            if len(packages) != 1 or packages[0]["version"] != old:
                raise ValueError("Version mismatch in uv.lock")
            pattern = r'(\[\[package\]\]\nname = "evidencekg"\nversion = ")[^"]+(")'
        else:
            pattern = r'(\[project\]\n(?:[^\[]*?\n)?version = ")[^"]+(")'
        updated, count = re.subn(pattern, lambda m: m[1] + version + m[2], content)
        if count != 1:
            raise ValueError(f"Cannot locate unique version in {relative}")
        changes[path] = updated
    for relative in ("product/ui/package.json", "product/ui/package-lock.json", "plugins/graf/.codex-plugin/plugin.json"):
        path = root / relative
        data = json.loads(path.read_text())
        if data["version"] != old:
            raise ValueError(f"Version mismatch in {relative}")
        data["version"] = version
        if relative.endswith("package-lock.json"):
            if data["packages"][""]["version"] != old:
                raise ValueError("Version mismatch in package-lock root")
            data["packages"][""]["version"] = version
        changes[path] = json.dumps(data, indent=2) + "\n"
    for path, content in changes.items():
        path.write_text(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Exact base version, e.g. 0.2.0")
    set_version(ROOT, parser.parse_args().version)
