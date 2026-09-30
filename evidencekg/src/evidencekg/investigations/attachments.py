"""Bounded prompt attachments, retained as source evidence rather than file paths."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path

from evidencekg.db import sha

TEXT_EXTRACTION_LIMITS = {
    "PDF visual content, layout and handwriting are not fully reviewed by text extraction",
    "DOCX formatting, revisions, embedded media and field evaluation require original visual review",
    "DOCX revision scopes preserved; formatting, revision acceptance, embedded media and field evaluation require original visual review",
}
SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".csv", ".json"}


def validate_attachments(attachments):
    if not isinstance(attachments, list) or len(attachments) > 5:
        raise ValueError("Attach at most 5 files")
    result, names, size = [], set(), 0
    for item in attachments:
        name, encoded = item.get("name"), item.get("data")
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 200
            or Path(name).name != name
            or any(c in name for c in ("/", "\\", "\x00"))
            or any(ord(c) < 32 for c in name)
            or Path(name).suffix.lower() not in SUFFIXES
        ):
            raise ValueError("Use a filename ending in TXT, MD, PDF, DOCX, CSV or JSON")
        if name in names:
            raise ValueError("Attachment filenames must be unique")
        if not isinstance(encoded, str) or len(encoded) > 2_796_204:
            raise ValueError("Each attachment must be at most 2 MiB")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid attachment encoding") from exc
        if not data or len(data) > 2 * 1024 * 1024:
            raise ValueError("Attachments must be nonempty and at most 2 MiB each")
        size += len(data)
        if size > 8 * 1024 * 1024:
            raise ValueError("Attachments must total at most 8 MiB")
        names.add(name)
        result.append((name, data))
    return result


def add_attachments(snapshot, uploads):
    from evidencekg.config import DEFAULTS
    from evidencekg.parsers import parse

    selected, total = [], 0
    for name, data in uploads:
        suffix = Path(name).suffix.lower()
        parsed = parse(
            data,
            ".txt" if suffix in {".csv", ".json"} else suffix,
            {**DEFAULTS, "parser_timeout": 30, "ocr": "off"},
        )
        warnings = parsed.get("warnings", [])
        if (
            parsed.get("status") not in {"ready", "partial"}
            or any(warning not in TEXT_EXTRACTION_LIMITS for warning in warnings)
            or parsed.get("attachments")
        ):
            raise ValueError(f"Could not read all of {name}. Upload a text export or a searchable PDF.")
        sections = [s for s in parsed.get("sections", []) if s.get("text", "").strip()]
        if not sections:
            raise ValueError(f"No readable text in {name}. Upload a text export or a searchable PDF.")
        document_id = "upload-" + sha(name.encode() + b"\0" + data)
        for index, section in enumerate(sections):
            text = section["text"]
            total += len(text.encode())
            if total > 48000:
                raise ValueError(
                    "Attached text exceeds 48,000 bytes. Attach a smaller excerpt; nothing was truncated."
                )
            key = document_id + "-" + str(index)
            snapshot["segments"][key] = {
                "id": key,
                "document_version_id": document_id,
                "text": text,
                "source_path": name,
                "locators": [section.get("locator", {})],
                "attachment": True,
                "extraction_warnings": warnings,
            }
            selected.append(key)
        snapshot["manifest"]["documents"].append(
            {
                "document_version_id": document_id,
                "path": name,
                "status": parsed["status"],
                "warnings": warnings,
            }
        )
        snapshot["originals"][document_id] = data
        snapshot.setdefault("capture_limitations", []).extend(f"{name}: {warning}" for warning in warnings)
    # Explicitly attached evidence takes precedence over retrieval candidates.
    snapshot["selected_segments"] = selected + snapshot["selected_segments"]
