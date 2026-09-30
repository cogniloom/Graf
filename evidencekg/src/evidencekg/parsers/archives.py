"""Read archive members as bytes, never materialize untrusted member paths."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import mimetypes
import stat
import tarfile
import zipfile
from pathlib import PurePosixPath

from .formats import ARCHIVES
from .payloads import write_payload


def extract_archive(data, suffix, cfg, directory):
    if suffix not in ARCHIVES:
        return None
    result = dict(sections=[], warnings=[], attachments=[], artifacts=[], status="ready")
    total = 0
    count = 0

    def member(name, size, reader, unsafe=False):
        nonlocal total, count
        if count >= cfg["max_attachments"]:
            raise ValueError("Archive descendants could not be enumerated: attachment count limit")
        part = f"archive:{count}"
        count += 1
        path = PurePosixPath(name.replace("\\", "/"))
        gap = None
        if unsafe or path.is_absolute() or ".." in path.parts or ":" in name or not path.name:
            gap = "Unsafe archive path, link or special member rejected"
        budget = min(cfg["max_file_bytes"], cfg["max_attachment_bytes"] - total)
        if size is not None and size > budget:
            gap = "Archive member expansion size limit reached"
        content = None
        if not gap:
            try:
                with reader() as source:
                    content = source.read(budget + 1)
                if len(content) > budget:
                    content = None
                    gap = "Archive member expansion size limit reached"
            except Exception as exc:
                gap = f"Archive member unreadable: {type(exc).__name__}: {str(exc)[:200]}"
        if content is not None:
            total += len(content)
        record = dict(
            name=path.name or "unreadable",
            part=part,
            mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
            **(write_payload(directory, content) if content is not None else {"data": None}),
        )
        if gap:
            record["gap"] = gap
            result["warnings"].append(f"{part}: {gap}; descendants could not be enumerated")
        result["attachments"].append(record)
        result["sections"].append(
            dict(
                text=name,
                locator={"kind": "archive_member", "part": part, "member_path": name},
                modality="native",
            )
        )

    try:
        if suffix == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for entry in archive.infolist():
                    if entry.is_dir():
                        continue
                    mode = entry.external_attr >> 16
                    member(
                        entry.filename,
                        entry.file_size,
                        lambda e=entry: archive.open(e),
                        unsafe=stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)),
                    )
        elif suffix in (".gz", ".gzip", ".bz2", ".bzip", ".bzip2", ".xz", ".emz"):
            opener = (
                gzip.GzipFile
                if suffix in (".gz", ".gzip", ".emz")
                else (bz2.BZ2File if suffix in (".bz2", ".bzip", ".bzip2") else lzma.LZMAFile)
            )
            member(
                "content.emf" if suffix == ".emz" else "content",
                None,
                lambda: (
                    gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb")
                    if opener is gzip.GzipFile
                    else opener(io.BytesIO(data), mode="rb")
                ),
            )
            if suffix == ".emz":
                result["warnings"].append(
                    "EMZ decompressed; EMF visual content is not rendered or OCR reviewed"
                )
        elif suffix in (".tar", ".tgz", ".tbz", ".tbz2", ".txz"):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r|*") as archive:
                for ordinal, entry in enumerate(archive):
                    if ordinal >= 20000:
                        raise ValueError("Archive entry enumeration limit")
                    if entry.isdir():
                        continue
                    member(
                        entry.name,
                        entry.size,
                        lambda e=entry: archive.extractfile(e),
                        unsafe=not entry.isfile(),
                    )
        else:
            import libarchive

            # libarchive streams decompressed blocks, unlike full-buffer 7z APIs.
            with libarchive.memory_reader(data) as archive:
                for ordinal, entry in enumerate(archive):
                    if ordinal >= 20000:
                        raise ValueError("Archive entry enumeration limit")
                    if entry.isdir:
                        continue

                    class Blocks(io.RawIOBase):
                        def read(self, limit=-1):
                            chunks = bytearray()
                            for block in entry.get_blocks():
                                chunks.extend(block[: max(0, limit - len(chunks))])
                                if len(chunks) >= limit:
                                    break
                            return bytes(chunks)

                    member(
                        entry.pathname, entry.size, Blocks, unsafe=not entry.isfile or bool(entry.linkpath)
                    )
    except Exception as exc:
        result["warnings"].append(f"Archive enumeration failed: {type(exc).__name__}: {str(exc)[:300]}")
    if result["warnings"]:
        result["status"] = "partial" if result["sections"] else "failed"
    return result
