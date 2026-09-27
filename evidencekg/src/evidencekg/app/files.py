"""Descriptor-pinned, read-only source inventory and stable staging copies."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

MAX_FILE_BYTES = 100_000_000


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


def read_file(fd, *, output=None, stopped=lambda: False):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Nonregular source entries are not allowed")
    if before.st_size > MAX_FILE_BYTES:
        raise ValueError("Source exceeds the 100 MB acquisition limit")
    digest, size = hashlib.sha256(), 0
    while True:
        if stopped():
            raise InterruptedError("Source scan stopped")
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise ValueError("Source exceeds the 100 MB acquisition limit")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    if signature(before) != signature(os.fstat(fd)) or size != before.st_size:
        raise ValueError("Source changed during acquisition")
    return {"sha256": digest.hexdigest(), "size": size}


def inventory(config, source, stopped=lambda: False):
    path = config.source(source["path"])
    rows = {}

    def walk(fd, prefix=""):
        before = signature(os.fstat(fd))
        with os.scandir(fd) as entries:
            names = sorted(e.name for e in entries)
        for name in names:
            if stopped():
                raise InterruptedError("Source scan stopped")
            rel = prefix + name
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                mode = os.fstat(child).st_mode
                if stat.S_ISDIR(mode):
                    walk(child, rel + "/")
                else:
                    rows[rel] = read_file(child, stopped=stopped)
                if signature(os.fstat(child)) != signature(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                    raise ValueError("Source changed during inventory")
            finally:
                os.close(child)
        if before != signature(os.fstat(fd)):
            raise ValueError("Directory changed during inventory")

    with opened(path, directory=source["kind"] == "directory") as fd:
        if source["kind"] == "directory":
            walk(fd)
        else:
            rows[path.name] = read_file(fd, stopped=stopped)
    blocked = restricted_hashes(config)
    paths = restricted_paths(config)
    return {
        name: item
        for name, item in rows.items()
        if item["sha256"] not in blocked
        and str(path / name if source["kind"] == "directory" else path) not in paths
    }


def stage(config, source, target, stopped=lambda: False):
    """Copy only the exact checksummed inventory; drift invalidates the entire build."""
    base = config.source(source["path"])
    count = 0
    blocked = restricted_hashes(config)
    paths = restricted_paths(config)
    for rel, expected in sorted(source["inventory"].items()):
        if expected["sha256"] in blocked:
            raise ValueError("Source became restricted after inventory; rebuild is required")
        original = base / rel if source["kind"] == "directory" else base
        if str(original) in paths:
            raise ValueError("Source path is restricted; rebuild is required")
        target_file = Path(target) / source["id"] / rel
        target_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with opened(original) as fd, target_file.open("xb") as output:
            actual = read_file(fd, output=output, stopped=stopped)
            output.flush()
            os.fsync(output.fileno())
        if actual != expected:
            raise ValueError("Source hash changed during staging")
        count += 1
    return count
