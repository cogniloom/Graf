"""Read-only haiku.rag 0.88 MCP adapter and deterministic extracted-text snapshots.

Offsets count Python Unicode characters. Unit start/end partition the whole text;
context_start/context_end add overlap. Snapshots never overwrite a destination.
Stable reads prove an observed snapshot, not that ingestion is caught up or paused.
No probe or snapshot call invokes search, an embedding provider, or an LLM.
"""
from __future__ import annotations

import asyncio
import ctypes
from collections import Counter
from contextlib import asynccontextmanager
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import stat
import tempfile
from urllib.parse import unquote, urlparse

TOOLS = ("list_documents", "get_document", "get_document_outline",
         "get_document_section", "search_documents")


class SnapshotError(RuntimeError):
    """A snapshot cannot be published safely."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _positive(config, name, default):
    value = config.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class HaikuClient:
    """Small allowlisted MCP client; does not expose mutating tools."""
    def __init__(self, client, config):
        self.client, self.config = client, config

    async def call(self, name, **arguments):
        if name not in TOOLS:
            raise ValueError("Tool is not allowlisted")
        result = await self.client.call_tool(name, arguments)
        if getattr(result, "is_error", False):
            raise SnapshotError(f"MCP {name} failed")
        data = getattr(result, "structured_content", None)
        if data is None:
            data = getattr(result, "data", None)
        if data is None:
            blocks = getattr(result, "content", [])
            text = [b.text for b in blocks if getattr(b, "type", None) == "text"]
            if len(text) != 1:
                raise SnapshotError(f"MCP {name} has no unambiguous JSON response")
            data = json.loads(text[0])
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        return data

    async def list(self):
        size = _positive(self.config, "page_size", 100)
        pages = _positive(self.config, "max_pages", 10000)
        documents, seen = [], set()
        offset = 0
        for _ in range(pages):
            page = await self.call("list_documents", limit=size, offset=offset)
            if not isinstance(page, list) or len(page) > size:
                raise SnapshotError("Invalid document page")
            if not page:
                return sorted(documents, key=lambda d: (d.get("source") or "", d["id"]))
            for doc in page:
                if not isinstance(doc, dict) or not isinstance(doc.get("id"), str) or not doc["id"]:
                    raise SnapshotError("Invalid document identity")
                key = (doc.get("source"), doc["id"])
                if key in seen:
                    raise SnapshotError("Repeated document or unstable pagination")
                seen.add(key)
                documents.append(doc)
            # Even a short page may reflect a server-side cap; stop only on empty.
            offset += len(page)
        raise SnapshotError("Document pagination limit exceeded")

    async def get(self, document_id, source=None):
        return await self.call("get_document", document_id=document_id, source=source)

    async def outline(self, document_id, source=None):
        return await self.call("get_document_outline", document_id=document_id, source=source)

    async def section(self, document_id, section_id, source=None):
        return await self.call("get_document_section", document_id=document_id,
                               section_id=section_id, source=source)

    async def search(self, query, limit=10):
        # Explicit caller action only: semantic search may use configured embeddings.
        return await self.call("search_documents", query=query, limit=limit, include_images=False)


@asynccontextmanager
async def connect(config):
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport
    url, command = config.get("mcp_url"), config.get("mcp_command")
    if url and command:
        raise ValueError("Choose mcp_url or mcp_command")
    with open(os.devnull, "w") as log:
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("MCP URL must be local HTTP(S)")
            transport = url
        else:
            if command is None:
                command = [str(Path(__file__).parent / ".venv/bin/haiku-rag"), "--read-only"]
                if config.get("haiku_config"):
                    command += ["--config", str(config["haiku_config"])]
                command += ["mcp", "--stdio"]
                if config.get("db_path"):
                    command += ["--db", str(config["db_path"])]
            if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
                raise ValueError("mcp_command must be a nonempty argv list")
            if "--read-only" not in command or "mcp" not in command or "--stdio" not in command:
                raise ValueError("Local command requires --read-only mcp --stdio")
            transport = StdioTransport(command=command[0], args=command[1:],
                                       keep_alive=False, log_file=log)
        async with Client(transport, timeout=config.get("timeout", 120)) as client:
            yield HaikuClient(client, config)


def _root(config):
    path = Path(config["source_root"]).expanduser()
    if path.is_symlink():
        raise SnapshotError("Source root must not be a symlink")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise SnapshotError("Source root must be a directory")
    return root


def _stat_signature(value):
    # Reading a file/directory can change atime on relatime mounts. Access time
    # is not a content mutation and must not invalidate our own read-only scan.
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def scan_sources(root):
    """Hash every regular file, account for symlinks/errors, never follow links."""
    result = {}
    def visit(directory):
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise SnapshotError("Source directory cannot be enumerated") from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                before = path.lstat()
                stamp = _stat_signature(before)
                if stat.S_ISLNK(before.st_mode):
                    result[relative] = {"status": "symlink", "source_sha256": None,
                                        "stamp": stamp, "target": os.readlink(path)}
                elif stat.S_ISDIR(before.st_mode):
                    visit(path)
                    if _stat_signature(path.lstat()) != stamp:
                        raise SnapshotError("Source directory changed during scan")
                elif stat.S_ISREG(before.st_mode):
                    digest = hashlib.sha256()
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(fd, "rb") as stream:
                        if _stat_signature(os.fstat(stream.fileno())) != stamp:
                            raise SnapshotError("Source changed before read")
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                        if _stat_signature(os.fstat(stream.fileno())) != stamp or _stat_signature(path.lstat()) != stamp:
                            raise SnapshotError("Source changed during read")
                    result[relative] = {"status": "file", "source_sha256": digest.hexdigest(), "stamp": stamp}
                else:
                    result[relative] = {"status": "error", "source_sha256": None,
                                        "stamp": stamp, "error": "unsupported_file_type"}
            except OSError as exc:
                result[relative] = {"status": "error", "source_sha256": None,
                                    "error": type(exc).__name__}
    root_before = root.stat()
    visit(root)
    if _stat_signature(root.stat()) != _stat_signature(root_before):
        raise SnapshotError("Source root changed during scan")
    return result


def split_text(document_id, text, size=10000, overlap=800):
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("Unit size must be positive")
    if isinstance(overlap, bool) or not isinstance(overlap, int) or not 0 <= overlap < size:
        raise ValueError("Overlap must be between zero and size minus one")
    units = []
    for sequence, start in enumerate(range(0, len(text), size)):
        end = min(start + size, len(text))
        context_start, context_end = max(0, start - overlap), min(len(text), end + overlap)
        units.append({"id": f"{document_id}:{sequence:06d}", "document_id": document_id,
                      "sequence": sequence, "start": start, "end": end,
                      "context_start": context_start, "context_end": context_end,
                      "text_sha256": _hash(text[context_start:context_end].encode("utf-8"))})
    return units


def _map_uri(uri, root):
    if not isinstance(uri, str) or not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if parsed.netloc not in ("", "localhost"):
        return None
    path = Path(unquote(parsed.path) if parsed.scheme else uri)
    if not path.is_absolute():
        path = root / path
    # lexical normalization avoids following a source symlink.
    path = Path(os.path.abspath(path))
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def reconcile(root, inventory, indexed):
    records, mapped, ids = [], set(), set()
    paths = Counter(_map_uri(doc.get("uri"), root) for doc in indexed)
    for doc in indexed:
        identity = doc["id"]
        if identity in ids:
            raise SnapshotError("Duplicate indexed document ID across collections")
        ids.add(identity)
        path = _map_uri(doc.get("uri"), root)
        source = inventory.get(path)
        status, error = "ok", None
        if path is None:
            status, error = "unmapped", "indexed_uri_outside_source_root"
        elif paths[path] > 1:
            status, error = "duplicate", "multiple_indexed_documents_for_source"
        elif source is None:
            status, error = "missing", "indexed_source_missing"
        elif source["status"] != "file":
            status, error = source["status"], source.get("error", "source_is_symlink")
        if path in inventory:
            mapped.add(path)
        records.append({"id": identity, "path": path, "source_sha256": source.get("source_sha256") if source else None,
                        "text_sha256": None, "text_file": None, "status": status, "error": error,
                        "metadata": doc.get("metadata") or {}, "source": doc.get("source"), "uri": doc.get("uri")})
    for path in sorted(set(inventory) - mapped):
        source = inventory[path]
        identity = "source:" + _hash(path.encode("utf-8"))
        if identity in ids:
            raise SnapshotError("Source and indexed identity collision")
        records.append({"id": identity, "path": path, "source_sha256": source.get("source_sha256"),
                        "text_sha256": None, "text_file": None, "status": "unindexed" if source["status"] == "file" else source["status"],
                        "error": source.get("error", "source_is_symlink" if source["status"] == "symlink" else "source_not_indexed"), "metadata": {}})
    for record in records:
        source = inventory.get(record["path"], {})
        record["size"] = source["stamp"][3] if "stamp" in source else None
        record["mime"] = mimetypes.guess_type(record["path"] or "")[0] or "application/octet-stream"
    return sorted(records, key=lambda r: (r["path"] or "", r["id"]))


async def probe(config: dict) -> dict:
    root = _root(config)
    inventory = await asyncio.to_thread(scan_sources, root)
    async with connect(config) as client:
        tools = await client.client.list_tools()
        available = {tool.name for tool in tools}
        indexed = await client.list() if "list_documents" in available else []
    records = reconcile(root, inventory, indexed)
    return {"tools": {name: name in available for name in TOOLS},
            "counts": dict(sorted(Counter(r["status"] for r in records).items())),
            "source_count": len(inventory), "indexed_count": len(indexed),
            "read_only": True, "ingestion_frozen": False}


async def snapshot(config: dict, destination: Path) -> dict:
    root = _root(config)
    if config.get("mcp_url"):
        raise SnapshotError("HTTP server cache freshness cannot be verified; use a local stdio command for snapshots")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Snapshot destination already exists")
    if destination.resolve().is_relative_to(root):
        raise SnapshotError("Snapshot destination must be outside source_root")
    size = _positive(config, "unit_chars", 10000)
    overlap = config.get("overlap_chars", 800)
    split_text("validation", "", size, overlap)
    before = await asyncio.to_thread(scan_sources, root)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=destination.parent))
    try:
        (staging / "texts").mkdir()
        async with connect(config) as client:
            indexed = await client.list()
            records = reconcile(root, before, indexed)
            if any(r["status"] in ("duplicate", "unmapped") for r in records):
                raise SnapshotError("Duplicate or unmapped indexed documents; refusing snapshot")
            fingerprints, units = {}, []
            for record in records:
                if record["status"] != "ok":
                    continue
                try:
                    document = await client.get(record["id"], record["source"])
                except Exception as exc:
                    raise SnapshotError("Cannot read indexed document") from exc
                if not isinstance(document, dict) or document.get("id") != record["id"] or not isinstance(document.get("content"), str):
                    raise SnapshotError("Invalid indexed document response")
                if _map_uri(document.get("uri"), root) != record["path"]:
                    raise SnapshotError("Indexed document mapping changed")
                fingerprints[record["id"]] = _hash(_canonical(document))
                text = document["content"]
                data = text.encode("utf-8")
                record["text_sha256"] = _hash(data)
                record["text_file"] = "texts/" + _hash(record["id"].encode("utf-8")) + ".txt"
                record["metadata"] = document.get("metadata") or {}
                record["provenance"] = "index_source_hash_unverified"
                if not text.strip():
                    record["status"], record["error"] = "empty", "indexed_text_empty"
                (staging / record["text_file"]).write_bytes(data)
                if record["status"] == "ok":
                    units.extend(split_text(record["id"], text, size, overlap))
        # A new stdio server avoids the 30-second default LanceDB reader cache.
        async with connect(config) as client:
            # Verify whole extracted payloads, not only enumeration or titles.
            for record in records:
                if record["id"] in fingerprints:
                    again = await client.get(record["id"], record["source"])
                    if _hash(_canonical(again)) != fingerprints[record["id"]]:
                        raise SnapshotError("Indexed content changed during snapshot")
            if await client.list() != indexed:
                raise SnapshotError("Index enumeration changed during snapshot")
        if await asyncio.to_thread(scan_sources, root) != before:
            raise SnapshotError("Sources changed during snapshot")
        counts = dict(sorted(Counter(r["status"] for r in records).items()))
        manifest = {"schema_version": 1, "source_root": str(root), "documents": records, "units": units,
                    "counts": counts, "frozen": True, "ingestion_frozen": False,
                    "complete": bool(records) and all(r["status"] == "ok" for r in records),
                    "source_index_provenance_verified": False,
                    "stability": "source hashes and full index payloads verified across two reads",
                    "unit_chars": size, "overlap_chars": overlap}
        (staging / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
        # Linux renameat2 publishes the complete directory atomically, without
        # replacing even an empty destination created by a concurrent caller.
        for file in staging.rglob("*"):
            if file.is_file():
                file.chmod(0o400)
        (staging / "texts").chmod(0o500)
        staging.chmod(0o500)
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise SnapshotError("Atomic no-replace publication requires renameat2")
        if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        return manifest
    finally:
        if staging.exists():
            staging.chmod(0o700)
            (staging / "texts").chmod(0o700)
            shutil.rmtree(staging)
