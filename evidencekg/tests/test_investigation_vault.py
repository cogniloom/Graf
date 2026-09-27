"""Real SQLite/filesystem/Ed25519 contracts, not physical-erasure or freshness proof."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evidencekg.investigations import Vault, verify_package
from evidencekg.investigations.verifier import canonical, content_name, digest


def add(vault, data=b"private source bytes", *, run="run-one", parents=(), name="sensitive-name.txt"):
    return vault.add_artifact(
        data, kind="source", name=name, media_type="text/plain", run_id=run, parents=parents
    )


@pytest.fixture
def vault(tmp_path):
    return Vault(tmp_path / "vault")


def object_path(vault, artifact):
    return vault.path / "objects" / content_name(artifact).split("/")[1]


def rewrite(source, destination, *, alter=None, omit=(), extra=()):
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(destination, "w") as after:
        for info in before.infolist():
            if info.filename not in omit:
                value = before.read(info)
                after.writestr(info, alter(info.filename, value) if alter else value)
        for name, value in extra:
            after.writestr(name, value)
    return destination


def signed_rewrite(vault, package, destination, mutate):
    def alter(name, value):
        if name != "manifest.json":
            return value
        envelope = json.loads(value)
        mutate(envelope["payload"])
        key = Ed25519PrivateKey.from_private_bytes((vault.path / "signing.key").read_bytes())
        envelope["signature"] = key.sign(canonical(envelope["payload"])).hex()
        return canonical(envelope)

    return rewrite(package, destination, alter=alter)


def test_roundtrip_scoping_trust_and_opaque_paths(vault, tmp_path):
    first = add(vault, name="../../must-not-be-a-path.txt")
    second = add(vault, b"derived", parents=[first["id"]])
    unrelated = add(vault, b"other run private bytes", run="run-two")
    event = vault.append_event(
        "run_completed", "user:local", "run-one", {"result_id": second["id"], "count": 2}
    )
    assert event["actor"] == "user:local"
    assert len(vault.artifacts("run-one")) == 2
    assert len(vault.relationships("run-one")) == 1
    assert first["id"] != unrelated["id"]
    assert vault.read_artifact(first["id"]) == b"private source bytes"
    metadata = vault.artifact(first["id"])
    metadata["name"] = "mutated outside vault"
    assert vault.artifact(first["id"])["name"] == "../../must-not-be-a-path.txt"
    assert not (tmp_path / "must-not-be-a-path.txt").exists()
    checkpoint = vault.checkpoint()
    package = vault.export(
        "run-one", tmp_path / "package.zip", {"format": "cannot-override", "note": "signed"}
    )
    report = verify_package(package)
    assert report["valid"] and report["integrity"] and not report["trusted"]
    assert verify_package(package, checkpoint["public_key"])["trusted"]
    assert verify_package(package, bytes.fromhex(checkpoint["public_key"]))["valid"]
    wrong_pin = verify_package(package, b"x" * 32)
    assert wrong_pin["integrity"] and not wrong_pin["valid"] and not wrong_pin["trusted"]
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))["payload"]
        assert manifest["extra"]["format"] == "cannot-override"
        assert unrelated["id"] not in {a["id"] for a in manifest["artifacts"]}
        assert content_name(unrelated) not in archive.namelist()
    assert Vault(vault.path).checkpoint() == checkpoint
    assert vault.verify()["integrity"]


@pytest.mark.parametrize("mode", ["modified", "missing", "truncated", "extra", "signature"])
def test_package_tampering(vault, tmp_path, mode):
    artifact = add(vault)
    package = vault.export("run-one", tmp_path / "good.zip")
    bad = tmp_path / "bad.zip"
    member = content_name(artifact)
    if mode == "missing":
        rewrite(package, bad, omit=[member])
    elif mode == "extra":
        rewrite(package, bad, extra=[("unlisted", b"hidden")])
    elif mode == "truncated":
        bad.write_bytes(package.read_bytes()[:-12])
    else:

        def alter(name, value):
            if mode == "modified" and name == member:
                return b"X" * len(value)
            if mode == "signature" and name == "manifest.json":
                envelope = json.loads(value)
                envelope["payload"]["extra"] = {"fake": "manifest"}
                return canonical(envelope)
            return value

        rewrite(package, bad, alter=alter)
    result = verify_package(bad)
    assert not result["valid"] and not result["integrity"] and result["errors"]


@pytest.mark.parametrize(
    "mode", ["false_deleted", "metadata", "omit_record", "truncate_ledger", "relationship"]
)
def test_verifier_replays_records_even_with_valid_outer_signature(vault, tmp_path, mode):
    first = add(vault)
    add(vault, b"derived", parents=[first["id"]])
    package = vault.export("run-one", tmp_path / "good.zip")

    def mutate(manifest):
        if mode == "false_deleted":
            manifest["artifacts"][0]["deleted"] = True
        elif mode == "metadata":
            manifest["artifacts"][0]["name"] = "changed"
        elif mode == "omit_record":
            manifest["artifacts"].pop()
        elif mode == "relationship":
            manifest["relationships"] = []
        else:
            manifest["events"].pop()

    bad = signed_rewrite(vault, package, tmp_path / "bad.zip", mutate)
    assert not verify_package(bad)["integrity"]


@pytest.mark.parametrize("filename", ["../escape", "/absolute", "artifacts/../escape", "back\\slash"])
def test_verifier_never_extracts_unsafe_zip_entries(vault, tmp_path, filename):
    add(vault)
    package = vault.export("run-one", tmp_path / "good.zip")
    bad = rewrite(package, tmp_path / "bad.zip", extra=[(filename, b"bad")])
    assert not verify_package(bad)["integrity"]
    assert not (tmp_path / "escape").exists()


def test_zip_duplicate_symlink_and_manifest_resource_limit(vault, tmp_path, monkeypatch):
    import evidencekg.investigations.verifier as verifier

    add(vault)
    package = vault.export("run-one", tmp_path / "good.zip")
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = rewrite(package, tmp_path / "duplicate.zip", extra=[("manifest.json", b"{}")])
    assert not verify_package(duplicate)["integrity"]
    symlink = zipfile.ZipInfo("link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert not verify_package(rewrite(package, tmp_path / "symlink.zip", extra=[(symlink, b"/etc/passwd")]))[
        "valid"
    ]
    monkeypatch.setattr(verifier, "MAX_MANIFEST", 10)
    assert "resource limits" in verify_package(package)["errors"][0]


def test_transitive_erasure_metadata_minimization_and_same_hash_independence(vault, tmp_path):
    first = add(vault)
    child = add(vault, b"child secret", parents=[first["id"]])
    grandchild = add(vault, b"grandchild secret", parents=[child["id"]])
    related = add(vault, b"explicit relationship derivative")
    vault.link(related["id"], grandchild["id"], "derived_from", "run-one")
    independent = add(vault, run="run-two")
    selected = {first["id"], child["id"], grandchild["id"], related["id"]}
    assert set(vault.preview_erase([first["id"]])["ids"]) == selected
    receipt = vault.erase([first["id"]], "administrator:1000", "Very private instruction contents")
    assert set(receipt["ids"]) == selected
    assert "physical" in receipt["limitations"]
    events = vault.events()
    assert events[-2]["event_type"] == "deletion_intent"
    assert events[-1]["event_type"] == "deletion_result"
    assert events[-1]["actor"] == events[-2]["actor"] == "administrator:1000"
    for artifact in vault.artifacts("run-one"):
        assert artifact["deleted"]
        assert set(artifact) == {"id", "sha256", "size", "run_id", "parents", "deleted"}
        assert not object_path(vault, artifact).exists()
        with pytest.raises(FileNotFoundError):
            vault.read_artifact(artifact["id"])
    assert vault.read_artifact(independent["id"]) == b"private source bytes"
    ledger = canonical(events)
    assert b"sensitive-name" not in ledger and b"Very private" not in ledger and b"child secret" not in ledger
    assert b"sensitive-name" in (vault.path / "ledger.sqlite3").read_bytes()  # other retained run metadata
    package = vault.export("run-one", tmp_path / "erased.zip")
    assert verify_package(package)["integrity"]
    with zipfile.ZipFile(package) as archive:
        assert archive.namelist() == ["manifest.json"]
    assert Vault(vault.path).verify()["integrity"]


@pytest.mark.parametrize("mode", ["missing", "modified", "truncated"])
def test_live_corruption_cannot_be_laundered_as_erasure(vault, mode):
    artifact = add(vault)
    path = object_path(vault, artifact)
    if mode == "missing":
        path.unlink()
    else:
        path.write_bytes(b"x" * (artifact["size"] if mode == "modified" else 1))
    assert not vault.verify()["integrity"]
    with pytest.raises((ValueError, FileNotFoundError)):
        vault.erase([artifact["id"]], "user:one", "authorized removal")
    assert not any(e["event_type"] == "deletion_intent" for e in vault.events())
    assert not vault.artifact(artifact["id"])["deleted"]


def test_deletion_recovery_after_real_process_exit(vault, tmp_path):
    first = add(vault)
    child = add(vault, b"derived bytes", parents=[first["id"]])
    script = """
import os, sys
from evidencekg.investigations import Vault
v=Vault(sys.argv[1])
original=v._unlink
def interrupted(objects, name):
    original(objects, name)
    os._exit(73)
v._unlink=interrupted
v.erase([sys.argv[2]], "user:crash-test", "private instruction")
"""
    process = subprocess.run([sys.executable, "-c", script, str(vault.path), first["id"]], check=False)
    assert process.returncode == 73
    with sqlite3.connect(vault.path / "ledger.sqlite3") as db:
        before = [json.loads(r[0]) for r in db.execute("SELECT record FROM events ORDER BY seq")]
    assert before[-1]["event_type"] == "deletion_intent"
    reopened = Vault(vault.path)
    assert all(reopened.artifact(a["id"])["deleted"] for a in (first, child))
    result = reopened.events()[-1]
    assert result["event_type"] == "deletion_result" and result["payload"]["recovered"]
    assert result["actor"] == "user:crash-test"
    assert reopened.verify()["integrity"]
    assert verify_package(reopened.export("run-one", tmp_path / "recovered.zip"))["integrity"]


def test_addition_transaction_failure_recovers_unpublished_blob(vault, monkeypatch):
    original = vault._seal

    def fail(*args):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(vault, "_seal", fail)
    with pytest.raises(RuntimeError, match="commit failure"):
        add(vault)
    assert list((vault.path / "objects").iterdir())
    monkeypatch.setattr(vault, "_seal", original)
    reopened = Vault(vault.path)
    assert reopened.artifacts() == [] and reopened.events() == []
    assert list((vault.path / "objects").iterdir()) == []
    assert reopened.verify()["integrity"]


def test_thread_and_process_writers_are_serialized(vault, tmp_path):
    def write(i):
        return add(Vault(vault.path), str(i).encode())["id"]

    with ThreadPoolExecutor(max_workers=6) as executor:
        thread_ids = list(executor.map(write, range(12)))
    assert len(set(thread_ids)) == 12
    script = """
import sys
from evidencekg.investigations import Vault
v=Vault(sys.argv[1])
for i in range(5):
    v.add_artifact((sys.argv[2]+str(i)).encode(), "source", "source.txt", "text/plain", "run-one")
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(vault.path), str(i)]) for i in range(4)]
    assert [p.wait(timeout=30) for p in processes] == [0, 0, 0, 0]
    events = vault.events()
    assert len(events) == len(vault.artifacts()) == 32
    assert [e["seq"] for e in events] == list(range(1, 33))
    assert vault.verify()["integrity"]
    assert verify_package(vault.export("run-one", tmp_path / "concurrent.zip"))["integrity"]


@pytest.mark.parametrize(
    "component",
    ["root", "objects", "signing.key", "ledger.sqlite3", "ledger.sqlite3-journal", "writer.lock", "payload"],
)
def test_symlink_fencing(vault, tmp_path, component):
    artifact = add(vault)
    outside = tmp_path / "outside"
    outside.write_bytes(b"must remain untouched")
    if component == "root":
        alias = tmp_path / "alias"
        alias.symlink_to(vault.path, target_is_directory=True)
        with pytest.raises((ValueError, OSError)):
            Vault(alias)
    else:
        path = object_path(vault, artifact) if component == "payload" else vault.path / component
        if component == "objects":
            saved = vault.path / "saved-objects"
            path.rename(saved)
            path.symlink_to(saved, target_is_directory=True)
        else:
            path.unlink(missing_ok=True)
            path.symlink_to(outside)
        with pytest.raises((ValueError, OSError)):
            vault.read_artifact(artifact["id"])
    assert outside.read_bytes() == b"must remain untouched"


def test_symlink_ancestor_and_export_destination_fencing(vault, tmp_path):
    add(vault)
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        Vault(alias / "vault")
    with pytest.raises(OSError):
        vault.export("run-one", alias / "export.zip")
    target = tmp_path / "target"
    target.write_bytes(b"retain")
    destination = tmp_path / "export.zip"
    destination.symlink_to(target)
    with pytest.raises(FileExistsError):
        vault.export("run-one", destination)
    assert target.read_bytes() == b"retain"
    with pytest.raises(ValueError):
        Vault(tmp_path / "real" / ".." / "escaped")


def test_hardlinks_and_root_replacement_are_refused(vault, tmp_path):
    artifact = add(vault)
    os.link(object_path(vault, artifact), tmp_path / "hardlink")
    with pytest.raises(ValueError, match="single link"):
        vault.read_artifact(artifact["id"])
    (tmp_path / "hardlink").unlink()
    original = vault.path
    vault.path.rename(tmp_path / "moved")
    Vault(original)
    with pytest.raises(ValueError, match="directory changed"):
        vault.artifacts()


@pytest.mark.parametrize("mode", ["metadata", "delete_flag", "chain", "checkpoint", "missing_db"])
def test_local_database_tampering_fails_closed(vault, mode):
    artifact = add(vault)
    db_path = vault.path / "ledger.sqlite3"
    if mode == "missing_db":
        db_path.unlink()
    else:
        with sqlite3.connect(db_path) as db:
            if mode in ("metadata", "delete_flag"):
                record = vault.artifact(artifact["id"])
                record["name" if mode == "metadata" else "deleted"] = "fake" if mode == "metadata" else True
                db.execute("UPDATE artifacts SET record=?", (canonical(record).decode(),))
            elif mode == "checkpoint":
                db.execute("DELETE FROM checkpoints")
            else:
                db.execute("DROP TRIGGER events_no_update")
                event = vault.events()[0]
                event["actor"] = "attacker"
                event["hash"] = digest({k: v for k, v in event.items() if k != "hash"})
                db.execute("UPDATE events SET record=?", (canonical(event).decode(),))
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        Vault(vault.path)


def test_ledger_append_only_and_content_minimization(vault):
    artifact = add(vault)
    with sqlite3.connect(vault.path / "ledger.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM events")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE events SET record='{}'")
    for event_type, payload in [
        ("artifact_added", {}),
        ("custom", {"text": "private"}),
        ("custom", {"value": "arbitrary private prose"}),
        ("custom", {"data": []}),
    ]:
        with pytest.raises(ValueError):
            vault.append_event(event_type, "user:one", "run-one", payload)
    vault.erase([artifact["id"]], "user:one", "private deletion instruction")
    raw = (vault.path / "ledger.sqlite3").read_bytes()
    assert b"sensitive-name.txt" not in raw and b"private deletion instruction" not in raw


def test_deleted_payload_reappearance_and_invalid_parents(vault):
    artifact = add(vault)
    vault.erase([artifact["id"]], "user:one", "remove")
    with pytest.raises(ValueError, match="retained"):
        add(vault, parents=[artifact["id"]])
    path = object_path(vault, artifact)
    path.write_bytes(b"private source bytes")
    path.chmod(0o600)
    assert not vault.verify()["integrity"]
    assert path.exists()  # Fail closed; do not silently rewrite deletion history.


def test_offline_cli_exit_status_and_pin(vault, tmp_path):
    add(vault)
    package = vault.export("run-one", tmp_path / "good.zip")
    command = [sys.executable, "-m", "evidencekg.investigations.verifier", str(package)]
    good = subprocess.run(
        command + ["--trusted-key", vault.checkpoint()["public_key"]], capture_output=True, text=True
    )
    assert good.returncode == 0 and json.loads(good.stdout)["trusted"]
    bad = subprocess.run(command + ["--trusted-key", "00" * 32], capture_output=True, text=True)
    assert bad.returncode == 1 and not json.loads(bad.stdout)["valid"]
