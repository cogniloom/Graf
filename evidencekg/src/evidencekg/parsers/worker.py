from __future__ import annotations

import base64
import io
import json
import resource
import subprocess
import sys
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path


def extract(data, suffix, cfg, directory):
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
        result["artifacts"].append(dict(name=name, data=base64.b64encode(content).decode()))

    def child(name, content, part, mime):
        if len(result["attachments"]) >= cfg["max_attachments"]:
            gap = "Attachment count limit reached at " + part
            result["warnings"].append(gap)
            result["attachments"].append(dict(name=name, data=None, part=part, mime=mime, gap=gap))
            return
        result["attachments"].append(
            dict(name=name, data=base64.b64encode(content).decode(), part=part, mime=mime)
        )

    def decode(payload, charset="utf-8-sig"):
        try:
            return payload.decode(charset)
        except (UnicodeError, LookupError):
            result["warnings"].append("Invalid/unknown text encoding; replacement characters retained")
            return payload.decode("utf-8", errors="replace")

    def ocr(image):
        if cfg["ocr"] != "auto":
            raise ValueError("OCR disabled")
        p = subprocess.run(
            ["tesseract", str(image), "stdout", "-l", cfg["ocr_languages"]]
            + (["--tessdata-dir", cfg["tessdata"]] if cfg.get("tessdata") else []),
            capture_output=True,
            timeout=90,
        )
        if p.returncode:
            raise ValueError(p.stderr.decode(errors="replace")[-1000:])
        text = p.stdout.decode("utf-8")
        if not text.strip():
            result["warnings"].append("OCR returned no readable text")
        return text

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
                        text = soup.get_text("\n")
                        result["warnings"].append("HTML MIME body formatting and visual content not reviewed")
                    add(text, {"kind": "mime_body", "mime_part": path})

        for i, (key, value) in enumerate(msg.raw_items()):
            add(str(value), {"kind": "email_header", "header": key, "index": i, "mime_part": "0"})
        visit(msg, "0")
        if result["attachments"]:
            result["warnings"].append(
                "Decoded MIME children are derived representations; exact wire bytes remain in parent original"
            )
    elif suffix == ".docx":
        from docx import Document

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 20000 or sum(x.file_size for x in infos) > cfg["max_file_bytes"] * 8:
                raise ValueError("Office archive expansion limit")
            # Preserve raw XML, including table/revision structure, as a separate artifact.
            for name in sorted(z.namelist()):
                if name.startswith("word/") and name.endswith(".xml"):
                    artifact(name, z.read(name))
            for name in sorted(z.namelist()):
                if name.startswith("word/embeddings/") and not name.endswith("/"):
                    child(Path(name).name, z.read(name), "docx:" + name, "application/octet-stream")
            document = Document(io.BytesIO(data))
            # XML iteration includes tables, text boxes, footnotes, comments and deleted text.
            from lxml import etree

            for name in sorted(z.namelist()):
                if name.startswith("word/") and name.endswith(".xml"):
                    root = etree.fromstring(
                        z.read(name), parser=etree.XMLParser(resolve_entities=False, no_network=True)
                    )
                    for i, paragraph in enumerate(root.xpath('//*[local-name()="p"]')):
                        tokens = []
                        for node in paragraph.iter():
                            tag = etree.QName(node).localname
                            if tag in ("t", "delText"):
                                tokens.append(node.text or "")
                            elif tag == "tab":
                                tokens.append("\t")
                            elif tag in ("br", "cr"):
                                tokens.append("\n")
                        add("".join(tokens), {"kind": "docx", "part": name, "paragraph": i})
            artifact(
                "tables.json",
                json.dumps([[[c.text for c in r.cells] for r in t.rows] for t in document.tables]).encode(),
            )
        result["warnings"].append(
            "DOCX formatting, revisions, embedded media and field evaluation require original visual review"
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
                except Exception as exc:
                    text = ""
                    result["warnings"].append(
                        f"Page {i}: native text failed; OCR/annotations still attempted: {exc}"
                    )
                method = "native"
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
                        text = ocr(image)
                        method = "ocr"
                    except Exception as exc:
                        result["warnings"].append(f"Page {i}: unread/OCR gap: {exc}")
                add(text, {"kind": "pdf", "page": i}, method)
                for j, annotation in enumerate(page.get("/Annots", [])):
                    obj = annotation.get_object()
                    for key in ("/Contents", "/T", "/V", "/Subj"):
                        if obj.get(key):
                            value = obj[key]
                            locator = {"kind": "pdf_annotation", "page": i, "annotation": j, "field": key}
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
                    add(ocr(target), {"kind": "image", "frame": i}, "ocr")
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
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
