"""Explicit offline administrative cleanup after an attributed source erasure.

Never invoked by an agent run or HTTP request. Requires the application stopped,
an administrative database config and a separately confirmed impact digest.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

import psycopg

from evidencekg.app.config import AppConfig, absolute
from evidencekg.db import dump, sha
from evidencekg.hybrid.postgres import _lock_id
from evidencekg.hybrid.runtime import database_dsn

from .vault import Vault


def plan(config, action_id, admin):
    path = absolute(config.home / "investigations" / "sessions.sqlite3")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        action = db.execute(
            "SELECT run_id,state,instruction_id FROM deletions WHERE id=?", (action_id,)
        ).fetchone()
        if not action or action[1] != "local_erasure_complete":
            raise ValueError("Complete the attributed local erasure before administrative cleanup")
        hashes = sorted(
            r[0] for r in db.execute("SELECT digest FROM restrictions WHERE action_id=?", (action_id,))
        )
    vault = Vault(config.home / "investigations" / "vault")
    instruction = vault.read_artifact(action[2])
    generations, snapshots = [], set()
    retained_scope = set()
    for artifact in vault.artifacts(action[0]):
        if artifact.get("kind") == "administrative_erasure_instruction" and not artifact.get("deleted"):
            previous = json.loads(vault.read_artifact(artifact["id"]))
            if previous["action_id"] == action_id:
                retained_scope.update(g["name"] for g in previous["generations"])
                snapshots.update(previous["snapshots"])
    root = config.home / "generations"
    for folder in sorted(root.iterdir()) if root.exists() else []:
        absolute(folder)
        if not folder.is_dir():
            raise ValueError("Unexpected generation entry")
        if any(p.is_symlink() for p in folder.rglob("*")):
            raise ValueError("Generation contains symlinks; manual investigation required")
        if folder.name not in retained_scope and not any(
            (folder / "vault" / "objects" / h[:2] / h).exists() for h in hashes
        ):
            continue
        database = absolute(folder / "vault" / "evidence.sqlite3")
        if database.exists():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                snapshots.update(r[0] for r in db.execute("SELECT id FROM snapshots"))
        files = {
            str(p.relative_to(folder)): sha(p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file()
        }
        generations.append({"name": folder.name, "files": files})
    # Retained retrieval results can duplicate passages. Bind every selected row
    # and its hash to the preview rather than deleting an unbounded cache.
    identities = set(snapshots)
    for snapshot in snapshots:
        row = admin.execute("SELECT manifest_sha FROM hybrid_snapshots WHERE id=%s", (snapshot,)).fetchone()
        if row:
            identities.add(row[0])
    caches = {}
    for table in ("hybrid_runs", "hybrid_results"):
        matches = []
        for key, identity in admin.execute(f"SELECT id,identity FROM {table}"):
            if any(identity_key in identity for identity_key in identities):
                matches.append({"id": key, "identity_sha": sha(identity)})
        caches[table] = sorted(matches, key=lambda item: item["id"])
    body = {
        "action_id": action_id,
        "run_id": action[0],
        "instruction_sha": sha(instruction),
        "generations": generations,
        "snapshots": sorted(snapshots),
        "caches": caches,
        "limitations": [
            "Whole affected generations are removed, including unrelated retained bytes in those generations.",
            "Original files and separately exported backups/packages are not deleted.",
            "Filesystem/database logical deletion does not prove forensic media erasure.",
        ],
    }
    return body | {"confirmation": sha(dump(body))}


def cleanup(config, admin_config, action_id, confirmation=None):
    """Preview by default. A matching digest authorizes this exact managed scope."""
    config = config if isinstance(config, AppConfig) else AppConfig.load(config)
    runtime_connection = json.loads(config.database_config.read_text())
    administrative_connection = json.loads(absolute(admin_config).read_text())
    if any(
        str(runtime_connection[k]) != str(administrative_connection[k]) for k in ("host", "port", "dbname")
    ):
        raise ValueError("Administrative and runtime configs must name the same database")
    admin_dsn = database_dsn(admin_config)
    with (config.home / "investigations" / "executor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Stop Graf before administrative erasure") from exc
        with psycopg.connect(admin_dsn, autocommit=True) as db:
            owner = sha(str(config.home))
            if not db.execute(
                "SELECT pg_try_advisory_lock(%s)", (_lock_id("docworm-worker:" + owner),)
            ).fetchone()[0]:
                raise ValueError("An index builder is active; stop Graf before erasure")
            preview = plan(config, action_id, db)
            if confirmation is None:
                return preview
            if confirmation != preview["confirmation"]:
                raise ValueError("Impact changed; review and confirm a fresh preview")
            if not preview["generations"] and not preview["snapshots"]:
                return {"status": "no_matching_managed_generations", "limitations": preview["limitations"]}
            vault = Vault(config.home / "investigations" / "vault")
            operation = uuid.uuid4().hex
            scope = vault.add_artifact(
                dump(preview).encode(),
                kind="administrative_erasure_instruction",
                name="cleanup-instruction.json",
                media_type="application/json",
                run_id=preview["run_id"],
                parents=(),
            )
            vault.append_event(
                "administrative_erasure_started",
                "administrator:uid:" + str(os.getuid()),
                preview["run_id"],
                {
                    "action_id": action_id,
                    "operation": operation,
                    "plan_sha": confirmation,
                    "instruction_id": scope["id"],
                },
            )
            vault.checkpoint()
            with db.transaction():
                db.execute(
                    "INSERT INTO docworm_erasure_authorizations(id,instruction_sha,actor) VALUES(%s,%s,%s)",
                    (operation, preview["instruction_sha"], "uid:" + str(os.getuid())),
                )
                db.execute("SELECT set_config('graf.erasure_action',%s,true)", (operation,))
                for item in preview["caches"]["hybrid_runs"]:
                    db.execute("DELETE FROM hybrid_reviews WHERE run=%s", (item["id"],))
                    db.execute("DELETE FROM hybrid_candidates WHERE run=%s", (item["id"],))
                    db.execute("DELETE FROM hybrid_runs WHERE id=%s", (item["id"],))
                for item in preview["caches"]["hybrid_results"]:
                    db.execute("DELETE FROM hybrid_results WHERE id=%s", (item["id"],))
                for snapshot in preview["snapshots"]:
                    for table in ("hybrid_edges", "hybrid_passages", "hybrid_documents"):
                        db.execute(f"DELETE FROM {table} WHERE snapshot=%s", (snapshot,))
                    db.execute("DELETE FROM hybrid_snapshots WHERE id=%s", (snapshot,))
                    db.execute("DELETE FROM docworm_worksets WHERE snapshot_id=%s", (snapshot,))
                db.execute(
                    "UPDATE docworm_workspace SET publication=NULL,snapshot_id=NULL,published_revision=NULL,"
                    "state='blocked',phase='erasure',message='Administrative erasure completed; rebuild required' "
                    "WHERE id=%s AND snapshot_id=ANY(%s)",
                    (owner, preview["snapshots"]),
                )
                db.execute(
                    "UPDATE docworm_erasure_authorizations SET completed_at=now() WHERE id=%s", (operation,)
                )
            # SQL deletion is committed first. Failures below retain the original
            # attributed instruction and a repeatable remaining-files preview.
            try:
                for generation in preview["generations"]:
                    folder = absolute(config.home / "generations" / generation["name"])
                    if any(p.is_symlink() for p in folder.rglob("*")):
                        raise ValueError("Generation changed after preview")
                    shutil.rmtree(folder)
                receipt = {
                    "action_id": action_id,
                    "operation": operation,
                    "plan_sha": confirmation,
                    "status": "managed_cleanup_completed",
                    "limitations": preview["limitations"],
                }
                artifact = vault.add_artifact(
                    dump(receipt).encode(),
                    kind="administrative_erasure_receipt",
                    name="cleanup.json",
                    media_type="application/json",
                    run_id=preview["run_id"],
                    parents=(),
                )
                vault.append_event(
                    "administrative_erasure_completed",
                    "administrator:uid:" + str(os.getuid()),
                    preview["run_id"],
                    {"receipt_id": artifact["id"], "operation": operation},
                )
                vault.checkpoint()
                return receipt
            except Exception:
                vault.append_event(
                    "administrative_erasure_incomplete",
                    "administrator:uid:" + str(os.getuid()),
                    preview["run_id"],
                    {"operation": operation},
                )
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--admin-config", type=Path, required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument(
        "--confirm", help="Exact digest from a reviewed preview; omission is read-only preview"
    )
    args = parser.parse_args()
    print(json.dumps(cleanup(args.config, args.admin_config, args.action, args.confirm), indent=2))


if __name__ == "__main__":
    main()
