"""Descriptor-pinned, read-only source inventory and stable staging copies."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from .config import absolute

# Reserved operational receipts emitted by Graf's corpus import helper. Ordinary
# logs, JSON, and metadata remain evidence. Explicit single-file selections do too.
IMPORT_RUNTIME_FILES = frozenset(
    {
        "graf-import.log",
        "graf-import-status.json",
        "graf-import.lock",
        "graf-document-verification.json",
    }
)


def import_runtime_file(path, mode):
    path = Path(path)
    name = path.name.removesuffix(".tmp")
    return stat.S_ISREG(mode) and path.parent.name == "metadata" and name in IMPORT_RUNTIME_FILES


def entry_signature(value):
    # Directory mtimes include atomic replacement of excluded bookkeeping files.
    # Recursion verifies included children; the parent pins directory identity.
    if stat.S_ISDIR(value.st_mode):
        return value.st_dev, value.st_ino, value.st_mode
    return signature(value)


def restricted_hashes(config):
    """Persist erasure exclusions across watcher scans and application restarts."""
    path = config.home / "investigations" / "sessions.sqlite3"
    if not path.exists():
        return set()
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Unsafe investigation restriction store")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return {r[0] for r in db.execute("SELECT digest FROM restrictions")}


def restricted_paths(config):
    path = config.home / "investigations" / "sessions.sqlite3"
    if not path.exists():
        return set()
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Unsafe investigation restriction store")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return {r[0] for r in db.execute("SELECT path FROM restricted_paths")}


@contextmanager
def opened(path, directory=False):
    """Open every absolute component without following symlinks, including roots."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe source path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory or i < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def signature(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def read_file(fd, *, output=None, stopped=lambda: False, on_bytes=None):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Nonregular source entries are not allowed")
    digest, size = hashlib.sha256(), 0
    while True:
        if stopped():
            raise InterruptedError("Source scan stopped")
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
        if on_bytes is not None:
            on_bytes(len(chunk))
    if signature(before) != signature(os.fstat(fd)) or size != before.st_size:
        raise ValueError("Source changed during acquisition")
    return {"sha256": digest.hexdigest(), "size": size}


def inventory(config, source, stopped=lambda: False, *, progress=None):
    path = config.source(source["path"])
    rows = {}
    checked_bytes = 0
    last_report = time.monotonic()
    current_file = ""

    def checked(size):
        nonlocal checked_bytes, last_report
        checked_bytes += size
        now = time.monotonic()
        if progress and now - last_report >= 1:
            progress(files_checked=len(rows), bytes_checked=checked_bytes, current_file=current_file)
            last_report = now

    def entries(fd, prefix):
        result = {}
        with os.scandir(fd) as entries:
            for entry in entries:
                location = path / (prefix + entry.name)
                if config.private_source(location):
                    continue
                try:
                    value = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    # The import helper can rename its .tmp receipt between
                    # scandir and stat. No evidence entry may disappear silently.
                    if import_runtime_file(location, stat.S_IFREG):
                        continue
                    raise
                if not import_runtime_file(location, value.st_mode):
                    result[entry.name] = entry_signature(value)
        return result

    def walk(fd, prefix=""):
        nonlocal current_file
        before = entries(fd, prefix)
        for name in sorted(before):
            if stopped():
                raise InterruptedError("Source scan stopped")
            rel = prefix + name
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                mode = os.fstat(child).st_mode
                if stat.S_ISDIR(mode):
                    walk(child, rel + "/")
                else:
                    current_file = rel
                    rows[rel] = read_file(child, stopped=stopped, on_bytes=checked)
                if entry_signature(os.fstat(child)) != entry_signature(
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                ):
                    raise ValueError("Source changed during inventory")
            finally:
                os.close(child)
        if before != entries(fd, prefix):
            raise ValueError("Directory changed during inventory")

    with opened(path, directory=source["kind"] == "directory") as fd:
        if source["kind"] == "directory":
            walk(fd)
        else:
            current_file = path.name
            rows[path.name] = read_file(fd, stopped=stopped, on_bytes=checked)
    if progress:
        progress(files_checked=len(rows), bytes_checked=checked_bytes, current_file=current_file)
    blocked = restricted_hashes(config)
    paths = restricted_paths(config)
    return {
        name: item
        for name, item in rows.items()
        if item["sha256"] not in blocked
        and str(path / name if source["kind"] == "directory" else path) not in paths
    }


def stage(config, source, target, stopped=lambda: False, *, progress=None):
    """Copy only the exact checksummed inventory; drift invalidates the entire build."""
    base = config.source(source["path"])
    count = 0
    copied_bytes = 0
    last_report = time.monotonic()
    last_count = 0
    current_file = ""

    def report(force=False):
        nonlocal last_report, last_count
        now = time.monotonic()
        if progress and (force or now - last_report >= 1 or count - last_count >= 100):
            progress(files=count, bytes_copied=copied_bytes, current_file=current_file)
            last_report, last_count = now, count

    def copied(size):
        nonlocal copied_bytes
        copied_bytes += size
        report()

    blocked = restricted_hashes(config)
    paths = restricted_paths(config)
    for rel, expected in sorted(source["inventory"].items()):
        if stopped():
            raise InterruptedError("Source staging stopped")
        if expected["sha256"] in blocked:
            raise ValueError("Source became restricted after inventory; rebuild is required")
        original = base / rel if source["kind"] == "directory" else base
        if str(original) in paths:
            raise ValueError("Source path is restricted; rebuild is required")
        target_file = Path(target) / source["id"] / rel
        current_file = rel
        target_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with opened(original) as fd, target_file.open("xb") as output:
            actual = read_file(fd, output=output, stopped=stopped, on_bytes=copied)
            output.flush()
            os.fsync(output.fileno())
        if actual != expected:
            raise ValueError("Source hash changed during staging")
        count += 1
        report()
    report(force=True)
    return count


def browse(config, value=None, offset=0, limit=200):
    """List local choices without reading files or following symlinks."""
    path = absolute(value) if value else absolute(Path.home())
    if config.private_source(path):
        raise ValueError("Graf workspace data cannot be selected")
    try:
        with opened(path, directory=True) as fd, os.scandir(fd) as entries:
            choices = []
            for entry in entries:
                child = path / entry.name
                if config.private_source(child):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    kind = "directory"
                elif entry.is_file(follow_symlinks=False):
                    kind = "file"
                else:
                    continue
                choices.append({"name": entry.name, "path": str(child), "kind": kind})
    except OSError as exc:
        raise ValueError(
            "Cannot open this folder. Check that it exists and you have permission to read it."
        ) from exc
    choices.sort(key=lambda item: (item["kind"] != "directory", item["name"].casefold(), item["name"]))
    return {
        "path": str(path),
        "parent": str(path.parent) if path.parent != path else None,
        "items": choices[offset : offset + limit],
        "total": len(choices),
    }
