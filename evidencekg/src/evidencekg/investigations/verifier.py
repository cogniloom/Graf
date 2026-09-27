"""Offline, non-extracting verification of Graf investigation packages.

A valid signature establishes integrity under the included key, not identity or
freshness. Pass a separately obtained raw Ed25519 public key (bytes or hex) to
establish signer trust. A valid historical package cannot prove it is the latest
checkpoint or that evidence was true, complete, or physically erased.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FORMAT = "graf-investigation-v1"
MAX_MANIFEST = 32 * 1024 * 1024
MAX_CONTENT = 2 * 1024 * 1024 * 1024
ID = re.compile(r"^[0-9a-f]{32}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def content_name(artifact):
    if not ID.fullmatch(artifact["id"]) or not HASH.fullmatch(artifact["sha256"]):
        raise ValueError("invalid artifact identity")
    return f"artifacts/{artifact['id']}-{artifact['sha256']}"


def verify_signature(envelope):
    payload = envelope["payload"]
    key = bytes.fromhex(envelope["public_key"])
    Ed25519PublicKey.from_public_bytes(key).verify(bytes.fromhex(envelope["signature"]), canonical(payload))
    return payload, key


def ledger_state(events):
    """Replay minimal records and validate attribution, order and deletion proofs."""
    previous = "0" * 64
    added, deleted, intents, links = {}, set(), {}, []
    operations, link_ids = set(), set()
    for seq, event in enumerate(events, 1):
        record = {k: v for k, v in event.items() if k != "hash"}
        if (
            set(event) != {"seq", "previous", "hash", "actor", "run_id", "event_type", "payload", "time"}
            or type(event["seq"]) is not int
            or event["seq"] != seq
            or event["previous"] != previous
            or event["hash"] != digest(record)
            or not isinstance(event["actor"], str)
            or not event["actor"]
            or not isinstance(event["run_id"], str)
            or not event["run_id"]
            or not isinstance(event["payload"], dict)
        ):
            raise ValueError("broken event chain")
        previous = event["hash"]
        payload = event["payload"]
        kind = event["event_type"]
        pending = set().union(*(i[0] for i in intents.values()))
        if kind == "artifact_added":
            aid = payload["id"]
            if (
                set(payload) != {"id", "sha256", "size", "parents", "metadata_hash"}
                or aid in added
                or not ID.fullmatch(aid)
                or not HASH.fullmatch(payload["sha256"])
                or not HASH.fullmatch(payload["metadata_hash"])
                or type(payload["size"]) is not int
                or payload["size"] < 0
                or not isinstance(payload["parents"], list)
                or len(payload["parents"]) != len(set(payload["parents"]))
            ):
                raise ValueError("duplicate or invalid artifact")
            if any(
                p not in added or p in deleted or p in pending or added[p]["run_id"] != event["run_id"]
                for p in payload["parents"]
            ):
                raise ValueError("invalid derivation")
            added[aid] = {**payload, "run_id": event["run_id"]}
        elif kind == "relationship_added":
            if (
                set(payload) != {"id", "source", "target", "relation"}
                or not ID.fullmatch(payload["id"])
                or payload["id"] in link_ids
                or not isinstance(payload["relation"], str)
                or not payload["relation"]
            ):
                raise ValueError("invalid relationship identity")
            link_ids.add(payload["id"])
            if any(
                a not in added or a in deleted or a in pending or added[a]["run_id"] != event["run_id"]
                for a in (payload["source"], payload["target"])
            ):
                raise ValueError("invalid relationship")
            links.append({**payload, "run_id": event["run_id"]})
        elif kind == "deletion_intent":
            if (
                set(payload) != {"operation", "ids", "reason_sha256"}
                or not ID.fullmatch(payload["operation"])
                or payload["operation"] in operations
                or not HASH.fullmatch(payload["reason_sha256"])
                or not isinstance(payload["ids"], list)
                or not payload["ids"]
                or len(payload["ids"]) != len(set(payload["ids"]))
            ):
                raise ValueError("invalid deletion intent")
            operations.add(payload["operation"])
            ids = set(payload["ids"])
            if not ids <= added.keys() or ids & deleted:
                raise ValueError("invalid deletion targets")
            # Every active descendant must be included in an authorized erasure.
            if any(
                a not in ids and a not in deleted and ids.intersection(m["parents"]) for a, m in added.items()
            ):
                raise ValueError("incomplete transitive deletion")
            if any(
                r["relation"] == "derived_from"
                and r["target"] in ids
                and r["source"] not in ids
                and r["source"] not in deleted
                for r in links
            ):
                raise ValueError("incomplete relationship-derived deletion")
            if any(ids.intersection(other[0]) for other in intents.values()):
                raise ValueError("overlapping deletion intents")
            intents[payload["operation"]] = (ids, event["actor"], event["run_id"])
        elif kind == "deletion_result":
            ids, actor, run_id = intents.pop(payload["operation"])
            if (
                set(payload) != {"operation", "ids", "status", "recovered"}
                or type(payload["recovered"]) is not bool
                or not isinstance(payload["ids"], list)
                or len(payload["ids"]) != len(set(payload["ids"]))
                or ids != set(payload["ids"])
                or actor != event["actor"]
                or run_id != event["run_id"]
                or payload["status"] != "deleted"
            ):
                raise ValueError("invalid deletion result")
            deleted.update(ids)
    return added, deleted, intents, links, previous


def validate_records(events, artifacts, relationships, run_id=None, *, allow_pending=False):
    added, deleted, intents, links, head = ledger_state(events)
    expected = {a: m for a, m in added.items() if run_id is None or m["run_id"] == run_id}
    records = {a["id"]: a for a in artifacts}
    if len(records) != len(artifacts) or records.keys() != expected.keys():
        raise ValueError("missing or unexpected artifact records")
    for aid, artifact in records.items():
        original = expected[aid]
        content_name(artifact)
        for field in ("sha256", "size", "parents", "run_id"):
            if artifact[field] != original[field]:
                raise ValueError("artifact identity changed")
        if type(artifact["deleted"]) is not bool or artifact["deleted"] != (aid in deleted):
            raise ValueError("artifact deletion lacks completed ledger proof")
        if not artifact["deleted"] and digest(artifact) != original["metadata_hash"]:
            raise ValueError("artifact metadata changed")
        if artifact["deleted"] and set(artifact) != {"id", "sha256", "size", "run_id", "parents", "deleted"}:
            raise ValueError("nonminimal tombstone")
    scoped_links = [r for r in links if run_id is None or r["run_id"] == run_id]
    if relationships != scoped_links:
        raise ValueError("relationships changed")
    if intents and not allow_pending:
        raise ValueError("incomplete deletion; recovery required")
    return head


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def verify_package(path: str | Path, trusted_key: bytes | str | None = None) -> dict:
    """Return {valid, integrity, trusted, errors, public_key, run_id} without extracting.

    ``valid`` requires integrity and, when supplied, a matching pinned key.
    ``trusted`` is false without a pin. Resource limits are deliberate fail-closed
    limits (32 MiB manifest, 2 GiB total uncompressed content, 100,000 entries).
    """
    result = {
        "valid": False,
        "integrity": False,
        "trusted": False,
        "errors": [],
        "public_key": None,
        "run_id": None,
    }
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > 100_000 or len(names) != len(set(names)):
                raise ValueError("duplicate or excessive ZIP entries")
            for entry in infos:
                mode = entry.external_attr >> 16
                if (
                    entry.is_dir()
                    or entry.flag_bits & 1
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or entry.filename.startswith("/")
                    or "\\" in entry.filename
                    or ".." in entry.filename.split("/")
                ):
                    raise ValueError("unsafe ZIP entry")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > MAX_MANIFEST or sum(i.file_size for i in infos) > MAX_CONTENT:
                raise ValueError("package exceeds verification resource limits")
            envelope = json.loads(archive.read(manifest_info), object_pairs_hook=_unique_object)
            manifest, key = verify_signature(envelope)
            if manifest["format"] != FORMAT:
                raise ValueError("unsupported package format")
            result.update(public_key=key.hex(), run_id=manifest["run_id"], checkpoint=manifest["checkpoint"])
            checkpoint, checkpoint_key = verify_signature(manifest["checkpoint"])
            if checkpoint_key != key or checkpoint["format"] != FORMAT:
                raise ValueError("checkpoint key or format mismatch")
            head = validate_records(
                manifest["events"], manifest["artifacts"], manifest["relationships"], manifest["run_id"]
            )
            if checkpoint["event_count"] != len(manifest["events"]) or checkpoint["head"] != head:
                raise ValueError("truncated or modified ledger")
            expected = {"manifest.json"}
            for artifact in manifest["artifacts"]:
                member = content_name(artifact)
                if artifact["deleted"]:
                    if member in names:
                        raise ValueError("deleted artifact content remains in package")
                    continue
                expected.add(member)
                info = archive.getinfo(member)
                if info.file_size != artifact["size"]:
                    raise ValueError("artifact size mismatch")
                hasher = hashlib.sha256()
                with archive.open(info) as stream:
                    while chunk := stream.read(1024 * 1024):
                        hasher.update(chunk)
                if hasher.hexdigest() != artifact["sha256"]:
                    raise ValueError("artifact hash mismatch")
            if set(names) != expected:
                raise ValueError("unexpected package entries")
            result["integrity"] = True
            if trusted_key is not None:
                pin = bytes.fromhex(trusted_key) if isinstance(trusted_key, str) else trusted_key
                result["trusted"] = pin == key
                if not result["trusted"]:
                    result["errors"].append("signer does not match pinned key")
            result["valid"] = trusted_key is None or result["trusted"]
    except Exception as exc:
        # Malformed/untrusted input is always a report, not an extraction or crash.
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result


def main(argv=None):
    """Offline CLI, with nonzero exit for integrity failure or pinned-key mismatch."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--trusted-key", help="Separately obtained raw Ed25519 public key as hex")
    args = parser.parse_args(argv)
    report = verify_package(args.package, args.trusted_key)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
