from __future__ import annotations

import json
import mimetypes
import time
import uuid
from pathlib import Path

from . import parsers
from .db import atomic, dump, ident, sha
from .features import index_extraction
from .inventory import capture, inventory
from .knowledge import enrich_document
from .knowledge import freeze as freeze_knowledge
from .knowledge_readings import compact_locator
from .segmentation import canonical, split


def ingest(store, reextract=False, progress=None, diagnostic=None):
    """Build a snapshot; progress describes acquisition, not snapshot publication.

    Callback errors propagate and abort the transaction rather than inventing progress.
    """
    cfg = store.config()
    items, errors = inventory(cfg["root"], cfg["excludes"])
    counts = dict(
        processed_files=0,
        total_files=len(items),
        processed_documents=0,
        failed_documents=0,
        partial_documents=0,
        unsupported_documents=0,
        stage="extracting",
        extraction_complete=False,
    )

    def report():
        if progress is not None:
            progress(dict(counts))

    if diagnostic is not None:
        diagnostic({"stage": "initializing"})
    report()
    parser_signature = parsers.signature(cfg)
    parser_hash = sha(dump(parser_signature))
    config_hash = sha(dump(cfg))
    corpus_id = store.one("SELECT id FROM corpora")["id"]
    manifest = dict(
        root=cfg["root"],
        inventory_complete=not errors,
        inventory_errors=errors,
        documents=[],
        parser=parser_signature,
        configuration=cfg,
        ruleset_hash=sha(dump({k: cfg[k] for k in ("identifiers", "names", "terms", "rules_version")})),
    )
    attachment_count = 0
    attachment_bytes = 0
    with store.write():
        latest = store.db.execute("SELECT snapshot_id FROM current_snapshot WHERE singleton=1").fetchone()
        previous_manifest = store.manifest(latest[0]) if latest else {"documents": [], "history": []}
        prior = {d["source_entry_id"]: d for d in previous_manifest["documents"]}

        def acquire(path, data, status="pending", warnings=None, parent=None, part=None, mime=None, depth=0):
            nonlocal attachment_count, attachment_bytes
            if diagnostic is not None:
                diagnostic({"stage": "extracting", "path": path})
            warnings = list(warnings or [])
            parent_entry = (
                store.one("SELECT source_entry_id FROM document_versions WHERE id=?", (parent,))[
                    "source_entry_id"
                ]
                if parent
                else None
            )
            identity_key = dump(["attachment", parent_entry, part]) if parent else dump(["filesystem", path])
            entry = ident("E", corpus_id, identity_key)
            store.db.execute(
                "INSERT OR IGNORE INTO source_entries VALUES(?,?,?,?,?)",
                (entry, corpus_id, path, time.time(), identity_key),
            )
            blob = store.put(data) if data is not None else None
            previous = prior.get(entry, {})
            if previous and previous.get("blob") == blob and previous.get("parent") == parent:
                version = previous["document_version_id"]
            else:
                version = ident("D", entry, blob, parent, part, previous.get("document_version_id"))
            store.db.execute(
                "INSERT OR IGNORE INTO document_versions VALUES(?,?,?,?,?,?,?,?)",
                (
                    version,
                    entry,
                    blob,
                    time.time(),
                    parent,
                    part,
                    mime or mimetypes.guess_type(path)[0],
                    previous.get("document_version_id"),
                ),
            )
            extraction = ident("X", version, parser_hash, config_hash, status, warnings)
            existing = store.db.execute("SELECT * FROM extractions WHERE id=?", (extraction,)).fetchone()
            if status == "pending":
                existing = store.db.execute(
                    "SELECT * FROM extractions WHERE document_version_id=? AND parser_version=? AND config_hash=? ORDER BY rowid DESC LIMIT 1",
                    (version, parser_hash, config_hash),
                ).fetchone()
                if existing and (existing["status"] == "failed" or reextract):
                    extraction = ident("X", extraction, "retry", uuid.uuid4().hex)
                    existing = None
                elif existing:
                    extraction = existing["id"]
            if existing:
                artifact = json.loads(store.get(existing["artifact_sha"]))
                status, warnings = existing["status"], json.loads(existing["warnings_json"])
                children = artifact["children"]
            else:
                if status == "pending":
                    result = parsers.parse(
                        data,
                        Path(path).suffix.lower(),
                        cfg
                        | {
                            "_transcription_model_identity": parser_signature.get(
                                "transcription_model_sha256"
                            ),
                            "_transcription_calibration_sha256": parser_signature.get(
                                "transcription_calibration_sha256"
                            ),
                        },
                    )
                    status = result["status"]
                    warnings += result["warnings"]
                else:
                    result = dict(sections=[], attachments=[], artifacts=[])
                text, locators = canonical(result["sections"])
                children = []
                for child in result["attachments"]:
                    payload = child.pop("data")
                    children.append(child | {"blob": store.put(payload) if payload is not None else None})
                    del payload
                artifacts = [
                    {k: v for k, v in a.items() if k != "data"} | {"blob": store.put(a["data"])}
                    for a in result["artifacts"]
                ]
                artifact = dict(
                    text=text,
                    configuration=cfg,
                    parser=parser_signature,
                    sections=result["sections"],
                    locators=locators,
                    children=children,
                    artifacts=artifacts,
                    empty=data is not None and len(data) == 0,
                )
                # Children now live in the vault. Do not retain every sibling payload
                # in each recursive acquisition frame.
                del result
                artifact_sha = store.put(dump(artifact))
                store.db.execute(
                    "INSERT INTO extractions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        extraction,
                        version,
                        "native-adapters",
                        parser_hash,
                        config_hash,
                        artifact_sha,
                        status,
                        dump(warnings),
                    ),
                )
                for ordinal, (start, end) in enumerate(
                    list(split(text, min(cfg["segment_chars"], (cfg["max_input_bytes"] - 8192) // 16)))
                    or [(0, 0)]
                ):
                    body = text[start:end]
                    segment = ident("S", extraction, ordinal, start, end)
                    locations = [loc for loc in locators if loc["end"] >= start and loc["start"] <= end]
                    # Full word geometry stays in the immutable extraction artifact.
                    # Repeating a whole OCR page in every passage can exhaust the
                    # research evidence budget before any text reaches the reader.
                    locations = [
                        dict(loc, locator=compact_locator(loc.get("locator", {}))) for loc in locations
                    ]
                    store.db.execute(
                        "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            segment,
                            extraction,
                            ordinal,
                            sha(body),
                            start,
                            end,
                            dump(locations),
                            "asr"
                            if any(location["modality"] == "asr" for location in locations)
                            else (
                                "ocr"
                                if any(location["modality"] == "ocr" for location in locations)
                                else "text"
                            ),
                            status,
                            body,
                        ),
                    )
                    store.db.execute("INSERT INTO segment_fts VALUES(?,?)", (segment, body))
                index_extraction(store, extraction, text, cfg, blob)
            unknown_descendants = any(
                "MIME nesting limit" in warning
                or "could not be enumerated" in warning
                or "enumeration failed" in warning
                for warning in warnings
            )
            if status == "failed":
                unknown_descendants = True
            if unknown_descendants:
                manifest["inventory_complete"] = False
                manifest["inventory_errors"].append(
                    {
                        "path": path,
                        "error": "Container descendants could not be fully enumerated; source denominator is unknown",
                    }
                )
            manifest["documents"].append(
                dict(
                    path=path,
                    source_entry_id=entry,
                    source_kind="attachment" if parent else "filesystem",
                    document_version_id=version,
                    extraction_id=extraction,
                    blob=blob,
                    status=status,
                    warnings=warnings,
                    parent=parent,
                    mime_part=part,
                    empty=artifact["empty"],
                    artifacts=[{"name": a["name"], "blob": a["blob"]} for a in artifact["artifacts"]],
                )
            )
            document = manifest["documents"][-1]
            document["knowledge"] = enrich_document(store, document, previous)
            counts["processed_documents"] += 1
            if status in ("failed", "partial", "unsupported"):
                counts[status + "_documents"] += 1
            for child in children:
                attachment_count += 1
                child_data = store.get(child["blob"]) if child["blob"] else None
                attachment_bytes += len(child_data or b"")
                gaps = [child["gap"]] if child.get("gap") else []
                if depth >= cfg["max_attachment_depth"]:
                    gaps.append("Attachment depth limit reached")
                elif (
                    attachment_count > cfg["max_attachments"]
                    or attachment_bytes > cfg["max_attachment_bytes"]
                ):
                    gaps.append("Attachment aggregate limit reached")
                elif len(child_data or b"") > cfg["max_file_bytes"]:
                    gaps.append("Attachment size limit reached")
                acquire(
                    path + "::" + child["part"] + "/" + Path(child["name"]).name,
                    child_data,
                    status="failed" if gaps else "pending",
                    warnings=gaps,
                    parent=version,
                    part=child["part"],
                    mime=child["mime"],
                    depth=depth + 1,
                )

        for item in items:
            # Independent originals have independent budgets. Every nested
            # descendant of this original still shares these same counters.
            attachment_count = attachment_bytes = 0
            if diagnostic is not None:
                diagnostic({"stage": "reading", "path": item["path"]})
            data = None
            warnings = [item["warning"]] if item["warning"] else []
            status = item["status"]
            if status == "pending":
                try:
                    data = capture(cfg["root"], item["path"], cfg["max_file_bytes"])
                except (OSError, ValueError) as exc:
                    status = "failed"
                    warnings.append(str(exc))
            acquire(item["path"], data, status, warnings)
            del data  # Source bytes are persisted; finalization must not retain them.
            counts["processed_files"] += 1
            report()
        if diagnostic is not None:
            diagnostic({"stage": "finalizing"})
        counts.update(stage="finalizing", extraction_complete=True)
        report()
        manifest["documents"].sort(key=lambda x: x["path"])
        previous_extracts = {
            d["document_version_id"]: d["extraction_id"]
            for d in previous_manifest["documents"] + previous_manifest.get("history", [])
        }
        history = {}
        active_ids = {d["document_version_id"] for d in manifest["documents"]}
        for doc in manifest["documents"]:
            version = store.one(
                "SELECT previous_version_id FROM document_versions WHERE id=?", (doc["document_version_id"],)
            )
            old = version["previous_version_id"]
            if old and old not in active_ids:
                extraction = previous_extracts.get(old)
                if not extraction:
                    extraction = store.one(
                        "SELECT id FROM extractions WHERE document_version_id=? ORDER BY rowid LIMIT 1",
                        (old,),
                    )["id"]
                history[old] = extraction
        manifest["history"] = [
            {"document_version_id": d, "extraction_id": x} for d, x in sorted(history.items())
        ]
        manifest["knowledge"] = freeze_knowledge(
            store, manifest["documents"], previous_manifest.get("knowledge")
        )
        manifest["ruleset_hash"] = sha(dump([manifest["ruleset_hash"], manifest["knowledge"]["signature"]]))
        manifest_sha = store.put(dump(manifest))
        snapshot = ident("N", manifest_sha, manifest["ruleset_hash"])
        store.db.execute(
            "INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)",
            (snapshot, manifest_sha, manifest["ruleset_hash"], time.time()),
        )
        for doc in manifest["documents"]:
            store.db.execute(
                "INSERT OR IGNORE INTO snapshot_documents VALUES(?,?,?)",
                (snapshot, doc["document_version_id"], doc["extraction_id"]),
            )
        for doc in manifest["history"]:
            store.db.execute(
                "INSERT OR IGNORE INTO snapshot_history VALUES(?,?,?)",
                (snapshot, doc["document_version_id"], doc["extraction_id"]),
            )
        store.db.execute(
            "INSERT OR IGNORE INTO snapshot_occurrences SELECT ?,o.id FROM occurrences o JOIN segments s ON s.id=o.segment_id JOIN snapshot_documents sd ON sd.extraction_id=s.extraction_id WHERE sd.snapshot_id=?",
            (snapshot, snapshot),
        )
        from .relationships import resolve

        resolve(store, snapshot)
        from .snapshots import freeze_fts

        freeze_fts(store, snapshot)
        store.db.execute(
            "INSERT INTO current_snapshot VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id",
            (snapshot,),
        )
        store.audit(
            "snapshot_created",
            {
                "snapshot_id": snapshot,
                "known_entries": len(manifest["documents"]),
                "inventory_complete": manifest["inventory_complete"],
            },
        )
    atomic(store.state / "status.json", dump({"snapshot_id": snapshot, **manifest}).encode())
    atomic(
        store.state / "status.md",
        (
            f"# Evidence inventory\n\nSnapshot: {snapshot}\nKnown entries: {len(manifest['documents'])}\nInventory complete: {manifest['inventory_complete']}\n\n"
            + "```json\n"
            + json.dumps(manifest["documents"], ensure_ascii=False, indent=2)
            + "\n```"
        ).encode(),
    )
    return snapshot
