from __future__ import annotations

import io
import json
import resource
import subprocess
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .ocr_readings import native_reading, tsv_reading
from .payloads import write_payload


def extract(data, suffix, cfg, directory):
    from .archives import extract_archive
    from .documents import extract_document
    from .formats import SUPPORTED, detect
    from .media import extract_media

    suffix = detect(data, suffix, cfg)
    if suffix == ".appledouble":
        from .appledouble import extract_appledouble

        return extract_appledouble(data)
    for adapter in (extract_archive, extract_document, extract_media):
        adapted = adapter(data, suffix, cfg, directory)
        if adapted is not None:
            return adapted
    if suffix not in SUPPORTED:
        return dict(
            sections=[],
            warnings=["Unsupported format: " + suffix],
            attachments=[],
            artifacts=[],
            status="unsupported",
        )
    if suffix in (".dotx", ".docm", ".dotm", ".doct"):
        suffix = ".docx"
    result = dict(sections=[], warnings=[], attachments=[], artifacts=[], status="ready")

    def add(text, locator, modality="native"):
        # email.raw_items() can expose surrogate-escaped undecodable header bytes.
        # Keep a reversible printable representation and a gap, never lose the body.
        if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
            result["warnings"].append(
                "Undecodable header/text bytes escaped in extracted representation; original bytes preserved"
            )
            text = text.encode("utf-8", errors="backslashreplace").decode("utf-8")
        result["sections"].append(dict(text=text, locator=locator, modality=modality))

    def artifact(name, content):
        result["artifacts"].append(dict(name=name, **write_payload(directory, content)))

    def child(name, content, part, mime):
        if len(result["attachments"]) >= cfg["max_attachments"]:
            gap = "Attachment count limit reached at " + part
            result["warnings"].append(gap)
            result["attachments"].append(dict(name=name, data=None, part=part, mime=mime, gap=gap))
            return
        result["attachments"].append(
            dict(name=name, **write_payload(directory, content), part=part, mime=mime)
        )

    def decode(payload, charset="utf-8-sig"):
        try:
            return payload.decode(charset)
        except (UnicodeError, LookupError):
            result["warnings"].append("Invalid/unknown text encoding; replacement characters retained")
            return payload.decode("utf-8", errors="replace")

    def ocr(image):
        from PIL import Image

        if cfg["ocr"] != "auto":
            raise ValueError("OCR disabled")
        output = image.with_suffix(".ocr")
        p = subprocess.run(
            ["tesseract", str(image), str(output), "-l", cfg["ocr_languages"]]
            + (["--tessdata-dir", cfg["tessdata"]] if cfg.get("tessdata") else [])
            + ["txt", "tsv"],
            capture_output=True,
            timeout=90,
        )
        if p.returncode:
            raise ValueError(p.stderr.decode(errors="replace")[-1000:])
        text = Path(str(output) + ".txt").read_bytes().decode("utf-8")
        artifact(output.name + ".txt", text.encode("utf-8"))
        try:
            tsv_bytes = Path(str(output) + ".tsv").read_bytes()
        except FileNotFoundError:
            tsv_bytes = b""
        artifact(output.name + ".tsv", tsv_bytes)
        tsv = tsv_bytes.decode("utf-8", errors="replace")
        with Image.open(image) as img:
            reading, warnings = tsv_reading(tsv, text, cfg["ocr_languages"], *img.size)
        result["warnings"].extend(f"{image.name}: {warning}" for warning in warnings)
        if not text.strip():
            result["warnings"].append("OCR returned no readable text")
        return text, reading

    if suffix in (".txt", ".md"):
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        add(decode(data, encoding), {"kind": "text"})
    elif suffix == ".eml":
        msg = BytesParser(policy=policy.default).parsebytes(data)

        def visit(part, path, depth=0):
            if depth > cfg["max_attachment_depth"]:
                gap = "MIME nesting limit: " + path
                result["warnings"].append(gap)
                result["attachments"].append(
                    dict(
                        name="unexamined-mime-container.eml",
                        data=None,
                        part=path,
                        mime=part.get_content_type(),
                        gap=gap,
                    )
                )
                return
            for defect in part.defects:
                result["warnings"].append("MIME defect at " + path + ": " + str(defect))
            if part.get_content_type() == "message/rfc822":
                nested = part.get_payload()
                if isinstance(nested, list):
                    for j, message in enumerate(nested):
                        child(
                            part.get_filename() or "message.eml",
                            message.as_bytes(policy=policy.default),
                            f"{path}.{j}",
                            "message/rfc822",
                        )
                else:
                    result["warnings"].append("Unread embedded message at " + path)
            elif part.is_multipart():
                for i, sub in enumerate(part.iter_parts()):
                    visit(sub, f"{path}.{i}", depth + 1)
            else:
                payload = part.get_payload(decode=True) or b""
                mime = part.get_content_type()
                if (
                    part.get_filename()
                    or part.get_content_disposition() == "attachment"
                    or mime not in ("text/plain", "text/html")
                ):
                    suffixes = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}
                    child(part.get_filename() or ("part" + suffixes.get(mime, ".bin")), payload, path, mime)
                else:
                    text = decode(payload, part.get_content_charset() or "utf-8")
                    if mime == "text/html":
                        from bs4 import BeautifulSoup

                        artifact("mime-" + path + ".html", payload)
                        soup = BeautifulSoup(text, "html.parser")
                        for node in soup(["script", "style"]):
                            node.decompose()
                        lines = []
                        for node in soup.find_all(string=True):
                            depth = sum(parent.name == "blockquote" for parent in node.parents)
                            for line in str(node).splitlines():
                                if line.strip():
                                    lines.append("> " * depth + line.strip())
                        text = "\n".join(lines)
                        result["warnings"].append("HTML MIME body formatting and visual content not reviewed")
                    from .structure import email_sections

                    for section in email_sections(
                        text, path, str(msg.get("From", "")), str(msg.get("Date", ""))
                    ):
                        add(section["text"], section["locator"])

        for i, (key, value) in enumerate(msg.raw_items()):
            add(str(value), {"kind": "email_header", "header": key, "index": i, "mime_part": "0"})
        visit(msg, "0")
        if result["attachments"]:
            result["warnings"].append(
                "Decoded MIME children are derived representations; exact wire bytes remain in parent original"
            )
    elif suffix == ".docx":
        from .documents import checked_zip

        with checked_zip(data, cfg) as z:
            infos = z.infolist()
            if (
                len(infos) > 20000
                or sum(x.file_size for x in infos) > min(cfg["max_file_bytes"], 100_000_000) * 8
            ):
                raise ValueError("Office archive expansion limit")
            # Preserve raw XML, including table/revision structure, as a separate artifact.
            for name in sorted(z.namelist()):
                if name.startswith("word/") and name.endswith(".xml"):
                    artifact(name, z.read(name))
            for name in sorted(z.namelist()):
                if name.startswith("word/embeddings/") and not name.endswith("/"):
                    child(Path(name).name, z.read(name), "docx:" + name, "application/octet-stream")
            # XML iteration includes tables, text boxes, footnotes, comments and deleted text.
            from lxml import etree

            tables = []
            for name in sorted(z.namelist()):
                if name.startswith("word/") and name.endswith(".xml"):
                    root = etree.fromstring(
                        z.read(name), parser=etree.XMLParser(resolve_entities=False, no_network=True)
                    )
                    if name == "word/document.xml":
                        for table in root.xpath('//*[local-name()="tbl"]'):
                            tables.append(
                                [
                                    [
                                        "\n".join(
                                            "".join(p.itertext()) for p in cell.xpath('./*[local-name()="p"]')
                                        )
                                        for cell in row.xpath('./*[local-name()="tc"]')
                                    ]
                                    for row in table.xpath('./*[local-name()="tr"]')
                                ]
                            )
                    from .structure import docx_sections

                    for section in docx_sections(root, name):
                        add(section["text"], section["locator"])
            artifact("tables.json", json.dumps(tables).encode())
        result["warnings"].append(
            "DOCX revision scopes preserved; formatting, revision acceptance, embedded media and field evaluation require original visual review"
        )
    elif suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("Encrypted PDF")
        result["warnings"].append(
            "PDF visual content, layout and handwriting are not fully reviewed by text extraction"
        )
        for i, page in enumerate(reader.pages, 1):
            try:
                try:
                    text = page.extract_text(extraction_mode="layout") or ""
                    runs = []

                    def capture_run(value, cm, tm, font, size):
                        if value.strip() and len(runs) < 20000:
                            runs.append(
                                dict(
                                    text=value,
                                    user_matrix=list(cm),
                                    text_matrix=list(tm),
                                    font_size=float(size),
                                )
                            )

                    try:
                        page.extract_text(visitor_text=capture_run)
                        artifact(
                            f"pdf-page-{i}-text-runs.json",
                            json.dumps(
                                {
                                    "runs": runs,
                                    "coordinate_status": "parser_estimate_unvalidated",
                                    "limit_reached": len(runs) >= 20000,
                                    "warning": "Coordinates and content-stream order may be wrong; no table or reading-order guarantee",
                                }
                            ).encode(),
                        )
                    except Exception:
                        result["warnings"].append(f"Page {i}: coordinate extraction unavailable")
                except Exception as exc:
                    text = ""
                    result["warnings"].append(
                        f"Page {i}: native text failed; OCR/annotations still attempted: {exc}"
                    )
                method = "native"
                reading = native_reading()
                if not text.strip():
                    try:
                        target = directory / f"page-{i}"
                        p = subprocess.run(
                            [
                                "pdftoppm",
                                "-f",
                                str(i),
                                "-l",
                                str(i),
                                "-scale-to",
                                "3000",
                                "-singlefile",
                                "-png",
                                str(directory / "input"),
                                str(target),
                            ],
                            capture_output=True,
                            timeout=60,
                        )
                        if p.returncode:
                            raise ValueError("PDF rendering failed")
                        image = target.with_suffix(".png")
                        artifact(f"page-{i}.png", image.read_bytes())
                        text, reading = ocr(image)
                        method = "ocr"
                        artifact(
                            f"page-{i}.ocr.json",
                            json.dumps(
                                {"locator": {"kind": "pdf", "page": i}, "text": text, "reading": reading},
                                ensure_ascii=False,
                            ).encode("utf-8"),
                        )
                    except Exception as exc:
                        result["warnings"].append(f"Page {i}: unread/OCR gap: {exc}")
                add(text, {"kind": "pdf", "page": i, "reading": reading}, method)
                for j, annotation in enumerate(page.get("/Annots", [])):
                    obj = annotation.get_object()
                    for key in ("/Contents", "/T", "/V", "/Subj"):
                        if obj.get(key):
                            value = obj[key]
                            locator = {
                                "kind": "pdf_annotation",
                                "page": i,
                                "annotation": j,
                                "field": key,
                                "reading": native_reading(),
                            }
                            if isinstance(value, str):
                                add(value, locator)
                            else:
                                # Signature dictionaries/binary values are not prose. Retain
                                # their representation separately instead of flooding review text.
                                name = f"page-{i}-annotation-{j}-{key[1:]}.json"
                                artifact(
                                    name,
                                    json.dumps({"locator": locator, "representation": repr(value)}).encode(),
                                )
                                result["warnings"].append(
                                    f"Non-text annotation retained as artifact {name}; semantics unreviewed"
                                )
            except Exception as exc:
                result["warnings"].append(f"Page {i}: extraction failed: {exc}")
        try:
            for name, values in reader.attachments.items():
                for i, value in enumerate(values):
                    child(name, value, f"pdf:{name}:{i}", "application/octet-stream")
        except Exception as exc:
            result["warnings"].append("PDF attachment enumeration failed: " + str(exc))
    else:
        from PIL import Image

        result["warnings"].append("Image OCR is fallible; visual content and handwriting remain unreviewed")
        with Image.open(io.BytesIO(data)) as img:
            for i in range(getattr(img, "n_frames", 1)):
                img.seek(i)
                target = directory / f"image-{i}.png"
                img.convert("RGB").save(target)
                artifact(target.name, target.read_bytes())
                try:
                    text, reading = ocr(target)
                    locator = {"kind": "image", "frame": i, "reading": reading}
                    add(text, locator, "ocr")
                    artifact(
                        f"image-{i}.ocr.json",
                        json.dumps(
                            {"locator": {"kind": "image", "frame": i}, "text": text, "reading": reading},
                            ensure_ascii=False,
                        ).encode("utf-8"),
                    )
                except Exception as exc:
                    result["warnings"].append(f"Frame {i}: OCR gap: {exc}")
    if result["warnings"]:
        result["status"] = "partial"
    return result


def main():
    from .isolation import deny_network

    deny_network()
    resource.setrlimit(resource.RLIMIT_AS, (2_000_000_000, 2_000_000_000))
    resource.setrlimit(resource.RLIMIT_CPU, (150, 150))
    resource.setrlimit(resource.RLIMIT_FSIZE, (500_000_000, 500_000_000))
    directory = Path(sys.argv[1])
    result = extract(
        (directory / "input").read_bytes(),
        sys.argv[2],
        json.loads((directory / "config.json").read_text()),
        directory,
    )
    with (directory / "result.json").open("w", encoding="utf-8") as output:
        json.dump(result, output)


if __name__ == "__main__":
    main()
