"""Unreviewed parser readings; scores are raw Tesseract values, never probabilities."""

from __future__ import annotations

import math


def native_reading():
    return dict(
        method="native",
        engine="pypdf",
        language=None,
        review_status="automatic_unreviewed",
        calibration_status="unavailable",
        words=[],
        image_width=None,
        image_height=None,
    )


def tsv_reading(tsv, text, language, image_width, image_height):
    """Align TSV words to unchanged TXT output from the same engine invocation.

    Offsets count Unicode code points, with an exclusive end. Malformed geometry
    is omitted, invalid scores become null, and uncertain alignment fails closed.
    The caller preserves both raw outputs as artifacts for inspection.
    """
    reading = dict(
        method="ocr",
        engine="tesseract",
        language=language,
        review_status="automatic_unreviewed",
        calibration_status="unvalidated",
        words=[],
        image_width=image_width,
        image_height=image_height,
    )
    warnings = []
    lines = tsv.splitlines()
    columns = "level page_num block_num par_num line_num word_num left top width height conf text".split()
    if not lines or lines[0].split("\t") != columns:
        return reading, ["OCR TSV header missing or invalid; word provenance unavailable"]
    cursor = 0
    for row_number, line in enumerate(lines[1:], 2):
        row = line.split("\t", 11)
        if len(row) != 12 or row[0] not in {"1", "2", "3", "4", "5"}:
            warnings.append(f"OCR TSV row {row_number}: malformed row; remaining word provenance omitted")
            break
        if row[0] != "5" or not row[11].strip():
            continue
        word = row[11]
        start = cursor
        while start < len(text) and text[start].isspace():
            start += 1
        end = start + len(word)
        if text[start:end] != word or (end < len(text) and not text[end].isspace()):
            warnings.append(
                f"OCR TSV row {row_number}: text alignment failed; remaining word provenance omitted"
            )
            break
        cursor = end
        try:
            left, top, width, height = map(int, row[6:10])
            if (
                min(left, top) < 0
                or min(width, height) <= 0
                or left + width > image_width
                or top + height > image_height
            ):
                raise ValueError("out of bounds")
        except ValueError:
            warnings.append(f"OCR TSV row {row_number}: invalid bounding box; word provenance omitted")
            continue
        try:
            score = float(row[10])
            if not math.isfinite(score) or not 0 <= score <= 100:
                score = None
        except ValueError:
            score = None
        reading["words"].append(
            dict(text=word, start=start, end=end, bbox=[left, top, width, height], score=score)
        )
    if text[cursor:].strip():
        warnings.append("OCR text has no complete TSV word coverage; text retained unchanged")
    return reading, warnings
