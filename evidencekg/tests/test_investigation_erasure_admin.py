"""Administrative cleanup of a disposable corpus, with explicit preview binding."""

import json
from pathlib import Path

import pytest
from test_app_lifecycle import app_database, manager, ready
from test_investigation_api import SyntheticAdapter

from evidencekg.investigations.erasure_admin import cleanup
from evidencekg.investigations.service import Investigations

__all__ = ["app_database", "manager"]


def test_managed_source_cleanup_requires_preview_and_preserves_original(manager):
    ready(manager)
    original = manager.config.allowed_roots[0] / "a.txt"
    original_bytes = original.read_bytes()
    service = Investigations(manager, SyntheticAdapter())
    run = service.create("Was the invoice approved?")
    service.execute(run["id"])
    source = next(a for a in service.vault.artifacts(run["id"]) if a.get("kind") == "source")
    preview = service.erasure_preview(run["id"], [source["id"]], "Synthetic court instruction")
    result = service.erase(
        run["id"],
        [source["id"]],
        "Synthetic court instruction",
        preview["preview_hash"],
        preview["confirmation"],
    )
    admin_config = Path(manager.config.database_config).with_name("admin.json")
    plan = cleanup(manager.config, admin_config, result["action_id"])
    assert plan["generations"] and plan["snapshots"]
    with pytest.raises(ValueError, match="Impact changed"):
        cleanup(manager.config, admin_config, result["action_id"], "wrong digest")
    receipt = cleanup(manager.config, admin_config, result["action_id"], plan["confirmation"])
    assert receipt["status"] == "managed_cleanup_completed"
    assert not list((manager.config.home / "generations").iterdir())
    assert original.read_bytes() == original_bytes
    assert manager.status()["state"] == "blocked"
    retained = json.loads(
        service.vault.read_artifact(
            next(
                a["id"]
                for a in service.vault.artifacts(run["id"])
                if a.get("kind") == "administrative_erasure_receipt"
            )
        )
    )
    assert retained["plan_sha"] == plan["confirmation"]


def test_partial_snapshot_cannot_bypass_restricted_path(manager):
    from evidencekg.app.manager import NotReady

    ready(manager)
    service = Investigations(manager, SyntheticAdapter())
    first = service.create("Synthetic first version")
    service.execute(first["id"])
    original = manager.config.allowed_roots[0] / "a.txt"
    original.write_text("Updated synthetic evidence. The invoice was not approved.\n")
    manager.reconcile()
    manager.run_once()
    source = next(a for a in service.vault.artifacts(first["id"]) if a.get("kind") == "source")
    preview = service.erasure_preview(first["id"], [source["id"]], "Synthetic order")
    service.erase(
        first["id"], [source["id"]], "Synthetic order", preview["preview_hash"], preview["confirmation"]
    )
    with pytest.raises(NotReady):
        service.create("Cannot revive through partial snapshot", allow_partial=True)


def test_admin_retry_keeps_generation_scope_after_original_blob_removed(manager, monkeypatch):
    import shutil

    ready(manager)
    service = Investigations(manager, SyntheticAdapter())
    run = service.create("Synthetic interrupted administrative erasure")
    service.execute(run["id"])
    source = next(a for a in service.vault.artifacts(run["id"]) if a.get("kind") == "source")
    preview = service.erasure_preview(run["id"], [source["id"]], "Synthetic order")
    action = service.erase(
        run["id"], [source["id"]], "Synthetic order", preview["preview_hash"], preview["confirmation"]
    )["action_id"]
    admin = Path(manager.config.database_config).with_name("admin.json")
    first = cleanup(manager.config, admin, action)
    original = shutil.rmtree

    def partial_remove(folder, *args, **kwargs):
        (folder / "vault" / "objects" / source["sha256"][:2] / source["sha256"]).unlink()
        raise OSError("Synthetic interruption after deleting original blob")

    monkeypatch.setattr("evidencekg.investigations.erasure_admin.shutil.rmtree", partial_remove)
    with pytest.raises(OSError):
        cleanup(manager.config, admin, action, first["confirmation"])
    monkeypatch.setattr("evidencekg.investigations.erasure_admin.shutil.rmtree", original)
    remaining = cleanup(manager.config, admin, action)
    assert [g["name"] for g in remaining["generations"]] == [g["name"] for g in first["generations"]]
    assert remaining["confirmation"] != first["confirmation"]
    assert (
        cleanup(manager.config, admin, action, remaining["confirmation"])["status"]
        == "managed_cleanup_completed"
    )
    assert not list((manager.config.home / "generations").iterdir())
