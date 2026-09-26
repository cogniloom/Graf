"""Consistent local recovery bundle. Caller holds launcher lock and stops the API."""

import hashlib
import json
import shutil
import uuid


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def backup(home, target, compose):
    target = target.expanduser().resolve()
    if target.is_relative_to(home) or home.is_relative_to(target):
        raise ValueError("Backup must be outside the workspace")
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with (target / "database.dump").open("xb") as out:
            compose(
                home, "exec", "-T", "postgres", "pg_dump", "-U", "postgres", "-Fc", "evidencekg", stdout=out
            )
        for name in (
            "app.json",
            "compose.json",
            "postgres.json",
            "evidencekg.json",
            "installed.json",
            "secrets",
            "generations",
        ):
            source = home / name
            if source.is_symlink():
                raise ValueError("Refusing symlink in workspace backup")
            if not source.exists():
                continue
            if source.is_dir():
                if any(p.is_symlink() for p in source.rglob("*")):
                    raise ValueError("Refusing symlink in workspace backup")
                shutil.copytree(source, target / name)
            else:
                if source.is_symlink():
                    raise ValueError("Refusing symlink in workspace backup")
                shutil.copy2(source, target / name)
        files = {str(p.relative_to(target)): digest(p) for p in sorted(target.rglob("*")) if p.is_file()}
        (target / "manifest.json").write_text(
            json.dumps({"format": 1, "home": str(home), "files": files}, indent=2)
        )
        print(f"Backup saved: {target}. Contains private evidence and credentials; protect this directory.")
    except BaseException:
        (target / "INCOMPLETE").touch()
        raise


def verify(home, target, compose):
    target = target.expanduser().resolve(strict=True)
    manifest = json.loads((target / "manifest.json").read_text())
    if (target / "INCOMPLETE").exists() or manifest["format"] != 1:
        raise ValueError("Incomplete or unsupported backup")
    for name, expected in manifest["files"].items():
        path = target / name
        if not path.resolve().is_relative_to(target) or path.is_symlink() or digest(path) != expected:
            raise ValueError("Backup checksum mismatch: " + name)
    database = "graf_verify_" + uuid.uuid4().hex
    compose(home, "exec", "-T", "postgres", "createdb", "-U", "postgres", database)
    try:
        with (target / "database.dump").open("rb") as stream:
            compose(
                home,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "-U",
                "postgres",
                "--exit-on-error",
                "--no-owner",
                "-d",
                database,
                stdin=stream,
            )
        compose(
            home,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            "SELECT state, revision, published_revision FROM docworm_workspace;",
        )
    finally:
        compose(home, "exec", "-T", "postgres", "dropdb", "-U", "postgres", database)
    print(
        "Checksums and PostgreSQL restore verified in an isolated temporary database. Original sources and model weights are not included."
    )
