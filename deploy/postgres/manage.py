"""Explicit Compose database migration, backup and isolated restore checks."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evidencekg/src"))
import psycopg  # noqa: E402
from evidencekg.hybrid.postgres import migrate  # noqa: E402
from evidencekg.hybrid.runtime import database_dsn  # noqa: E402
from psycopg import sql  # noqa: E402


def compose(*args, **kwargs):
    return subprocess.run(
        ["docker", "compose", "--project-directory", str(ROOT), "exec", "-T", "postgres", *args],
        check=True,
        **kwargs,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["migrate", "backup", "verify-backup"])
    p.add_argument("--file", type=Path)
    args = p.parse_args()
    os.umask(0o077)
    dsn = database_dsn(ROOT / ".evidencekg-private/postgres/postgres.json")
    if args.action == "migrate":
        migrate(dsn)
        print("PostgreSQL schema ready")
    elif args.action == "backup":
        if not args.file:
            p.error("--file is required")
        args.file.parent.mkdir(parents=True, exist_ok=True)
        with args.file.open("xb") as out:
            compose("pg_dump", "-U", "postgres", "-Fc", "evidencekg", stdout=out)
        digest = hashlib.sha256(args.file.read_bytes()).hexdigest()
        args.file.with_suffix(args.file.suffix + ".sha256").write_text(digest + "\n")
        print(json.dumps({"backup": str(args.file), "sha256": digest}))
    else:
        if not args.file:
            p.error("--file is required")
        digest = hashlib.sha256(args.file.read_bytes()).hexdigest()
        if digest != args.file.with_suffix(args.file.suffix + ".sha256").read_text().strip():
            raise ValueError("Backup checksum mismatch")
        target = "evidencekg_restore_" + uuid.uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
            try:
                with args.file.open("rb") as data:
                    compose(
                        "pg_restore",
                        "-U",
                        "postgres",
                        "--exit-on-error",
                        "--no-owner",
                        "--dbname",
                        target,
                        stdin=data,
                    )
                from evidencekg.hybrid.postgres import PostgresWorkset
                from psycopg.conninfo import make_conninfo

                restored = PostgresWorkset(make_conninfo(dsn, dbname=target))
                try:
                    with psycopg.connect(make_conninfo(dsn, dbname=target)) as check:
                        snapshots = check.execute("SELECT id FROM hybrid_snapshots").fetchall()
                        worksets = check.execute("SELECT id FROM hybrid_runs").fetchall()
                        results = check.execute("SELECT identity FROM hybrid_results").fetchall()
                    for (sid,) in snapshots:
                        restored.load_snapshot(sid)
                    for (key,) in worksets:
                        restored.page(key, limit=1)
                    for (identity,) in results:
                        restored.get_result(json.loads(identity))
                finally:
                    restored.close()
                print(
                    json.dumps(
                        {"restored_and_verified_snapshots": len(snapshots), "isolated_database": target}
                    )
                )
            finally:
                db.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))


if __name__ == "__main__":
    main()
