"""Inventory AppleDouble metadata without mistaking sidecars for PDF/image data.

Format: RFC 1740 appendices A/B. The original bytes stay in the evidence vault.
"""

import json
import struct


def extract_appledouble(data):
    if len(data) < 26:
        raise ValueError("Truncated AppleDouble header")
    magic, version = struct.unpack_from(">II", data)
    if magic != 0x00051607 or version != 0x00020000:
        raise ValueError("Unsupported AppleDouble header version")
    count = struct.unpack_from(">H", data, 24)[0]
    table_end = 26 + count * 12
    if table_end > len(data):
        raise ValueError("Truncated AppleDouble entry table")
    entries = []
    identifiers = set()
    spans = []
    for index in range(count):
        entry_id, offset, length = struct.unpack_from(">III", data, 26 + index * 12)
        if entry_id == 1 or entry_id in identifiers:
            raise ValueError("Invalid AppleDouble entry identity")
        if offset < table_end or offset + length > len(data):
            raise ValueError("AppleDouble entry outside original bytes")
        if length and any(offset < end and offset + length > start for start, end in spans):
            raise ValueError("Overlapping AppleDouble entries")
        identifiers.add(entry_id)
        if length:
            spans.append((offset, offset + length))
        entries.append({"entry_id": entry_id, "offset": offset, "length": length})
    return {
        "status": "partial",
        "sections": [
            {
                "text": json.dumps({"format": "AppleDouble", "version": 2, "entries": entries}),
                "locator": {"kind": "appledouble_metadata"},
                "modality": "native",
            }
        ],
        "warnings": [
            "AppleDouble metadata sidecar: entry inventory extracted; resource and Finder metadata "
            "semantics unreviewed. Document data is stored separately, not in this sidecar."
        ],
        "attachments": [],
        "artifacts": [],
    }
