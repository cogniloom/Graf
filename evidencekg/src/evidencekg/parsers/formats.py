"""Content detection performed inside the bounded, network-denied parser worker."""

from __future__ import annotations

import io
import tarfile
import zipfile

TEXT = set(
    ".txt .md .rst .log .csv .tsv .json .jsonl .ndjson .xml .yaml .yml .toml .ini .cfg .conf .sql .py .js .jsx .ts .tsx .css .scss .sh .bash .c .h .cpp .hpp .java .rs .go .rb .php .swift .kt .dart .tex .srt .vtt .vcf".split()
)
DOCUMENTS = set(
    ".html .htm .xhtml .ics .rtf .xlsx .xls .xlt .xltx .xlsm .xltm .xlst .doc .dot .docx .dotx .docm .dotm .doct .pptx .pptm .potx .odt .ods .odp .epub".split()
)
ARCHIVES = set(".zip .tar .gz .gzip .bz2 .bzip .bzip2 .xz .tgz .tbz .tbz2 .txz .7z .7zip .rar .emz".split())
MEDIA = set(
    ".wav .wave .mp3 .flac .ogg .oga .opus .m4a .aac .aiff .aif .wma .mp4 .m4v .mov .mkv .webm .avi .wmv .m4b .amr .au".split()
)
IMAGES = set(".png .jpg .jpeg .tif .tiff .bmp .webp .gif .ico .ppm .pgm .pbm".split())
SUPPORTED = TEXT | DOCUMENTS | ARCHIVES | MEDIA | IMAGES | {".pdf", ".eml"}


def detect(data, suffix, cfg):
    suffix = suffix.lower()
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 20000:
                raise ValueError("Archive enumeration failed: entry limit")
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return ".docx"
            if "xl/workbook.xml" in names:
                return ".xlsx"
            if "ppt/presentation.xml" in names:
                return ".pptx"
            if "content.xml" in names and "mimetype" in names:
                info = archive.getinfo("mimetype")
                if info.file_size < 256:
                    mime = archive.read(info)
                    for kind in ("text", "spreadsheet", "presentation"):
                        if mime == ("application/vnd.oasis.opendocument." + kind).encode():
                            return {"text": ".odt", "spreadsheet": ".ods", "presentation": ".odp"}[kind]
            if "META-INF/container.xml" in names:
                return ".epub"
        return ".zip"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        import olefile

        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if ole.exists("WordDocument"):
                return ".doc"
            if ole.exists("Workbook") or ole.exists("Book"):
                return ".xls"
        return ".ole"  # Unknown OLE containers are never decoded as text.
    for magic, kind in (
        (b"7z\xbc\xaf\x27\x1c", ".7z"),
        (b"Rar!\x1a\x07", ".rar"),
        (b"\x1f\x8b", ".gz"),
        (b"BZh", ".bz2"),
        (b"\xfd7zXZ\x00", ".xz"),
        (b"\x89PNG\r\n\x1a\n", ".png"),
        (b"\xff\xd8\xff", ".jpg"),
        (b"GIF87a", ".gif"),
        (b"GIF89a", ".gif"),
        (b"II*\x00", ".tif"),
        (b"MM\x00*", ".tif"),
        (b"fLaC", ".flac"),
        (b"OggS", ".ogg"),
        (b"ID3", ".mp3"),
    ):
        if data.startswith(magic):
            return ".emz" if kind == ".gz" and suffix == ".emz" else kind
    if len(data) > 262 and data[257:262] == b"ustar":
        return ".tar"
    if len(data) >= 1024 and len(data) % 512 == 0:
        try:
            tarfile.TarInfo.frombuf(data[:512], encoding="utf-8", errors="surrogateescape")
            return ".tar"
        except tarfile.HeaderError:
            pass
    if data.startswith(b"RIFF"):
        return {b"WAVE": ".wav", b"WEBP": ".webp", b"AVI ": ".avi"}.get(data[8:12], suffix)
    if len(data) > 12 and data[4:8] == b"ftyp":
        return ".mp4"
    if data.lstrip().startswith(b"{\\rtf"):
        return ".rtf"
    head = data[:4096].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return ".html"
    if head.startswith(b"begin:vcalendar"):
        return ".ics"
    headers = head.replace(b"\r\n", b"\n").split(b"\n\n", 1)[0]
    if (b"mime-version:" in headers and b"content-type:" in headers) or (
        b"message-id:" in headers and (headers.startswith(b"from:") or b"\nfrom:" in headers)
    ):
        return ".eml"
    if suffix in SUPPORTED:
        return suffix
    # Strict detection prevents arbitrary binary bytes becoming invented text.
    try:
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = data.decode(encoding)
    except UnicodeError:
        return suffix
    if text and all(c.isprintable() or c in "\r\n\t\f" for c in text):
        return ".txt"
    return suffix
