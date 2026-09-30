"""Versioned compressed knowledge artifacts; original evidence storage is unchanged."""

from __future__ import annotations

import json
import zlib

from .db import dump

MAGIC = b"GRAF-KNOWLEDGE-ZLIB-1\n"
MAX_DECODED_BYTES = 512 * 1024 * 1024


def put(store, value):
    raw = dump(value).encode()
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError("Knowledge artifact exceeds decoded storage budget")
    return store.put(MAGIC + zlib.compress(raw, level=6))


def read(store, digest):
    raw = store.get(digest)  # Content hash verification remains owned by Store.
    if raw.startswith(MAGIC):
        decoder = zlib.decompressobj()
        try:
            decoded = decoder.decompress(raw[len(MAGIC) :], MAX_DECODED_BYTES + 1)
        except zlib.error as exc:
            raise ValueError("Invalid compressed knowledge artifact") from exc
        if len(decoded) > MAX_DECODED_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError("Invalid or oversized compressed knowledge artifact")
        raw = decoded
    return json.loads(raw)  # Original uncompressed generations remain readable.
