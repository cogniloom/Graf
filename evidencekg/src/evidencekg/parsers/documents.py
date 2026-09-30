"""Deterministic document representations with explicit fidelity limitations."""

from __future__ import annotations

import csv
import io
import json
import subprocess
import zipfile

from .formats import DOCUMENTS, TEXT


def checked_zip(data, cfg):
    archive = zipfile.ZipFile(io.BytesIO(data))
    entries = archive.infolist()
    if len(entries) > 20000 or sum(e.file_size for e in entries) > cfg["max_attachment_bytes"]:
        archive.close()
        raise ValueError("Office archive expansion limit; descendants could not be enumerated")
    if any(e.file_size > cfg["max_file_bytes"] for e in entries):
        archive.close()
        raise ValueError("Office member expansion limit; descendants could not be enumerated")
    return archive


def extract_document(data, suffix, cfg, directory):
    if suffix not in DOCUMENTS | TEXT or suffix in (".docx", ".dotx", ".docm", ".dotm", ".doct"):
        return None
    result = dict(sections=[], warnings=[], attachments=[], artifacts=[], status="ready")

    def add(text, **locator):
        result["sections"].append(dict(text=text, locator=locator, modality="native"))

    def decode():
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            return data.decode(encoding)
        except UnicodeError:
            result["warnings"].append("Invalid/unknown text encoding; replacement characters retained")
            return data.decode("utf-8", errors="replace")

    if suffix in (".html", ".htm", ".xhtml"):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(data, "html.parser")
        for node in soup(["script", "style", "template"]):
            node.decompose()
        add(soup.get_text("\n"), kind="html")
        result["warnings"].append("HTML layout, scripts, remote resources and visual content not evaluated")
    elif suffix == ".rtf":
        from striprtf.striprtf import rtf_to_text

        add(rtf_to_text(decode(), errors="replace"), kind="rtf")
        result["warnings"].append(
            "RTF formatting and embedded objects not reviewed; descendants could not be enumerated"
        )
    elif suffix == ".ics":
        # Unfold lines per RFC 5545, preserve properties/parameters/timezones verbatim.
        text = decode().replace("\r\n", "\n").replace("\n ", "").replace("\n\t", "")
        add(text, kind="calendar_properties")
        result["warnings"].append(
            "Calendar properties preserved; recurrence, timezone and alarms not evaluated"
        )
    elif suffix in (".csv", ".tsv"):
        for row, fields in enumerate(
            csv.reader(io.StringIO(decode()), delimiter="\t" if suffix == ".tsv" else ","), 1
        ):
            add(json.dumps(fields, ensure_ascii=False), kind="delimited_row", row=row)
    elif suffix in (".xlsx", ".xltx", ".xlsm", ".xltm", ".xlst"):
        from openpyxl import load_workbook

        with checked_zip(data, cfg):
            book = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
            cached = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
            try:
                for sheet in book:
                    # Ignore declared dimensions: malformed spreadsheets can claim 1M x 16K cells.
                    sheet.reset_dimensions()
                    cached[sheet.title].reset_dimensions()
                    cached_rows = iter(cached[sheet.title].iter_rows())
                    header = []
                    cells = 0
                    for row in sheet.iter_rows():
                        values = next(cached_rows, ())
                        if not header:
                            header = [str(c.value) if c.value is not None else None for c in row]
                        cells += len(row)
                        if cells > 1_000_000:
                            raise ValueError("Spreadsheet cell limit")
                        for cell in row:
                            if cell.value is not None:
                                add(
                                    str(cell.value),
                                    kind="spreadsheet_cell",
                                    sheet=sheet.title,
                                    cell=cell.coordinate,
                                    row=cell.row,
                                    column=cell.column,
                                    number_format=cell.number_format,
                                    formula=cell.value if cell.data_type == "f" else None,
                                    cached_value=str(values[cell.column - 1].value)
                                    if cell.data_type == "f" and len(values) >= cell.column
                                    and values[cell.column - 1].value is not None else None,
                                    header_candidate=header[cell.column - 1]
                                    if cell.row > 1 and len(header) >= cell.column else None,
                                    header_status="first_row_candidate_not_confirmed",
                                )
            finally:
                book.close()
                cached.close()
        result["warnings"].append(
            "Spreadsheet formulas retained, not calculated; charts, macros and embedded objects unreviewed; descendants could not be enumerated"
        )
    elif suffix in (".xls", ".xlt"):
        import xlrd

        book = xlrd.open_workbook(file_contents=data, on_demand=True)
        try:
            for sheet in book.sheets():
                if sheet.nrows * sheet.ncols > 1_000_000:
                    raise ValueError("Spreadsheet cell limit")
                for row in range(sheet.nrows):
                    for col in range(sheet.ncols):
                        cell = sheet.cell(row, col)
                        if cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                            value = cell.value
                            if cell.ctype == xlrd.XL_CELL_DATE:
                                value = xlrd.xldate_as_datetime(value, book.datemode).isoformat()
                            add(
                                str(value),
                                kind="spreadsheet_cell",
                                sheet=sheet.name,
                                row=row + 1,
                                column=col + 1,
                            )
        finally:
            book.release_resources()
        result["warnings"].append(
            "Legacy spreadsheet cached values may be stale; macros/charts/objects unreviewed; descendants could not be enumerated"
        )
    elif suffix in (".doc", ".dot"):
        completed = subprocess.run(
            ["antiword", "-m", "UTF-8.txt", str(directory / "input")],
            capture_output=True,
            timeout=min(cfg["parser_timeout"], 60),
        )
        if completed.returncode:
            raise ValueError("Legacy Word extraction failed (encrypted, damaged or unsupported document)")
        add(completed.stdout.decode("utf-8", errors="replace"), kind="legacy_word")
        result["warnings"].append(
            "Legacy Word text only; layout, revisions and embedded objects unreviewed; descendants could not be enumerated"
        )
    elif suffix in (".pptx", ".pptm", ".potx", ".odt", ".ods", ".odp", ".epub"):
        from defusedxml import ElementTree

        with checked_zip(data, cfg) as archive:
            for name in sorted(archive.namelist()):
                if suffix.startswith(".ppt") or suffix == ".potx":
                    selected = name.startswith(
                        ("ppt/slides/slide", "ppt/notesSlides/notesSlide")
                    ) and name.endswith(".xml")
                elif suffix == ".epub":
                    selected = name.lower().endswith((".html", ".xhtml", ".htm"))
                else:
                    selected = name == "content.xml"
                if not selected:
                    continue
                content = archive.read(name)
                if suffix == ".epub":
                    from bs4 import BeautifulSoup

                    soup = BeautifulSoup(content, "html.parser")
                    for node in soup(["script", "style"]):
                        node.decompose()
                    add(soup.get_text("\n"), kind="epub_part", part=name)
                else:
                    root = ElementTree.fromstring(content)
                    # Preserve paragraph and cell boundaries and do not expand repeated ODF cells.
                    tags = {"p", "h"} if suffix in (".odt", ".ods", ".odp") else {"t"}
                    for index, element in enumerate(root.iter()):
                        if element.tag.rsplit("}", 1)[-1] in tags:
                            add("".join(element.itertext()), kind="office_xml", part=name, element=index)
        result["warnings"].append(
            "Office/book text only; layout, repeated cells, formulas, media and embedded objects unreviewed; descendants could not be enumerated"
        )
    else:
        add(decode(), kind="text")
    if result["warnings"]:
        result["status"] = "partial"
    return result
