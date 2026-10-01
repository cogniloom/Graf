"""Versioned compressed knowledge artifacts; original evidence storage is unchanged."""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from itertools import islice, zip_longest

from .db import dump

MAGIC = b"GRAF-KNOWLEDGE-ZLIB-1\n"
MAX_DECODED_BYTES = 512 * 1024 * 1024


def put(store, value):
    raw = dump(value).encode()
    if len(raw) > MAX_DECODED_BYTES:
        raise ValueError("Knowledge artifact exceeds decoded storage budget")
    return store.put(MAGIC + zlib.compress(raw, level=6))


def read(store, digest, *, _chunk=False):
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
    value = json.loads(raw)  # Original uncompressed generations remain readable.
    if isinstance(value, dict) and value.get("format") == GRAPH_FORMAT:
        if _chunk:
            raise ValueError("Nested knowledge graph chunk")
        if set(value) != {"format", "metadata", "nodes", "edges"} or not isinstance(value["metadata"], dict):
            raise ValueError("Invalid knowledge graph manifest")
        if "nodes" in value["metadata"] or "edges" in value["metadata"]:
            raise ValueError("Invalid knowledge graph metadata")
        result = dict(value["metadata"])
        for kind in ("nodes", "edges"):
            if not isinstance(value[kind], list):
                raise ValueError("Invalid knowledge graph chunks")
            result[kind] = ShardedRows(store, value[kind])
        return result
    return value


GRAPH_FORMAT = "graf-knowledge-graph-v1"
GRAPH_CHUNK_BYTES = 2 * 1024 * 1024


def put_graph(store, graph):
    """Persist sorted graph streams as independently bounded, hash-bound chunks.

    A corpus graph must not require one corpus-sized JSON string, byte buffer,
    or decompression allocation. Document artifacts retain their existing format.
    """
    manifest = {
        "format": GRAPH_FORMAT,
        "metadata": {k: v for k, v in graph.items() if k not in {"nodes", "edges"}},
    }
    for kind in ("nodes", "edges"):
        chunks, rows, size = [], [], 2

        def flush():
            raw = ("[" + ",".join(rows) + "]").encode()
            if len(raw) > MAX_DECODED_BYTES:
                raise ValueError("Knowledge graph row exceeds decoded storage budget")
            chunks.append({"blob": store.put(MAGIC + zlib.compress(raw, level=6)), "count": len(rows)})

        for row in graph[kind]:
            encoded = dump(row)
            length = len(encoded.encode()) + 1
            if rows and size + length > GRAPH_CHUNK_BYTES:
                flush()
                rows, size = [], 2
            rows.append(encoded)
            size += length
        if rows:
            flush()
        manifest[kind] = chunks
    return put(store, manifest)


class ShardedRows(Sequence):
    """Replayable bounded reads; legacy unsharded artifacts still return lists."""

    def __init__(self, store, chunks):
        for chunk in chunks:
            if (
                not isinstance(chunk, dict)
                or set(chunk) != {"blob", "count"}
                or not isinstance(chunk["blob"], str)
                or len(chunk["blob"]) != 64
                or any(c not in "0123456789abcdef" for c in chunk["blob"])
                or type(chunk["count"]) is not int
                or chunk["count"] <= 0
            ):
                raise ValueError("Invalid knowledge graph chunk")
        self.store, self.chunks = store, chunks

    def __len__(self):
        return sum(chunk["count"] for chunk in self.chunks)

    def _read(self, chunk):
        part = read(self.store, chunk["blob"], _chunk=True)
        if (
            not isinstance(part, list)
            or len(part) != chunk["count"]
            or any(not isinstance(row, dict) for row in part)
        ):
            raise ValueError("Knowledge graph chunk count or row mismatch")
        return part

    def __iter__(self):
        for chunk in self.chunks:
            yield from self._read(chunk)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step > 0:
                return list(islice(self, start, stop, step))
            indices = range(start, stop, step)
            if not indices:
                return []
            return list(islice(self, indices[-1], indices[0] + 1, -step))[::-1]
        if index < 0:
            index += len(self)
        if index < 0:
            raise IndexError(index)
        for chunk in self.chunks:
            if index < chunk["count"]:
                return self._read(chunk)[index]
            index -= chunk["count"]
        raise IndexError(index)

    def __eq__(self, other):
        if not hasattr(other, "__iter__"):
            return NotImplemented
        missing = object()
        return all(a == b for a, b in zip_longest(self, other, fillvalue=missing))
