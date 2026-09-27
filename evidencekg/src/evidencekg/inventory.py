import fnmatch
import os
import stat
from pathlib import Path


def inventory(root, excludes):
    """Enumerate without following symlinks. Directory errors invalidate denominator."""
    root = Path(root)
    items, errors = [], []

    def walk(directory):
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda e: e.name)
        except OSError as exc:
            errors.append({"path": str(Path(directory).relative_to(root)), "error": str(exc)})
            return
        for entry in entries:
            rel = str(Path(entry.path).relative_to(root))
            try:
                if entry.is_symlink():
                    items.append(dict(path=rel, status="excluded", warning="Symlink not followed"))
                    # Descendants could exist, including outside scope; no complete denominator.
                    errors.append(dict(path=rel, error="Symlink target scope not enumerated"))
                elif entry.is_dir(follow_symlinks=False):
                    walk(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    excluded = any(fnmatch.fnmatchcase(rel, p) for p in excludes)
                    items.append(
                        dict(
                            path=rel,
                            status="excluded" if excluded else "pending",
                            warning="Configured exclusion" if excluded else "",
                        )
                    )
                else:
                    items.append(dict(path=rel, status="excluded", warning="Nonregular filesystem entry"))
            except OSError as exc:
                items.append(dict(path=rel, status="failed", warning=str(exc)))
                errors.append(dict(path=rel, error=str(exc)))

    walk(root)
    return items, errors


def capture(root, relative, limit):
    """Open every path component relative to pinned directory FDs, without symlinks."""
    parts = Path(relative).parts
    if not parts or any(p in ("..", ".") for p in parts) or Path(relative).is_absolute():
        raise ValueError("Unsafe source path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(source)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Source is no longer a regular file")
            if before.st_size > limit:
                raise ValueError("File-size acquisition limit reached")
            with os.fdopen(os.dup(source), "rb") as stream:
                data = stream.read(min(limit, before.st_size) + 1)
            after = os.fstat(source)
            current = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)

            def sig(s):
                return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

            if sig(before) != sig(after) or sig(after) != sig(current) or len(data) != before.st_size:
                raise ValueError("Source changed during acquisition; retry ingestion")
            if len(data) > limit:
                raise ValueError("File-size acquisition limit reached")
            return data
        finally:
            os.close(source)
    finally:
        os.close(fd)
