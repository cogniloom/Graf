import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from evidencekg.config import DEFAULTS
from evidencekg.parsers import parse
from evidencekg.parsers.ocr_readings import native_reading, tsv_reading

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def row(text, score="92.125", box=(10, 20, 30, 15)):
    return "\t".join(map(str, (5, 1, 1, 1, 1, 1, *box, score, text)))


def reading(rows, text):
    return tsv_reading("\n".join([HEADER, *rows]), text, "eng+deu", 800, 600)


def test_offsets_preserve_unicode_whitespace_and_repeated_words():
    text = "  Grüße\t😀\n\nGrüße e\u0301\n"
    result, warnings = reading([row(w) for w in ("Grüße", "😀", "Grüße", "e\u0301")], text)
    assert not warnings
    assert [w["start"] for w in result["words"]] == [2, 8, 11, 17]
    for word in result["words"]:
        assert text[word["start"] : word["end"]] == word["text"]
        assert word["bbox"] == [10, 20, 30, 15]
        assert word["score"] == 92.125
    assert result["review_status"] == "automatic_unreviewed"
    assert result["calibration_status"] == "unvalidated"
    assert result["language"] == "eng+deu"
    assert (result["image_width"], result["image_height"]) == (800, 600)


@pytest.mark.parametrize(
    "score, expected",
    [
        ("0", 0),
        ("100", 100),
        ("0.82", 0.82),
        ("-1", None),
        ("101", None),
        ("NaN", None),
        ("inf", None),
        ("bad", None),
        ("", None),
    ],
)
def test_raw_scores_are_not_probabilities(score, expected):
    result, warnings = reading([row("word", score)], "word\n")
    assert not warnings
    assert result["words"][0]["score"] == expected
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("tsv", ["", "garbage", HEADER + "\n5\tbroken"])
def test_bad_tsv_reports_gap_without_inventing_words(tsv):
    result, warnings = tsv_reading(tsv, "retained", "eng", 800, 600)
    assert result["words"] == []
    assert warnings


@pytest.mark.parametrize(
    "box", [(-1, 0, 1, 1), (0, 0, 0, 10), (799, 0, 2, 2), (0, 599, 2, 2), ("oops", 0, 1, 1)]
)
def test_invalid_geometry_omitted_but_following_alignment_retained(box):
    result, warnings = reading([row("bad", box=box), row("good")], "bad good")
    assert [w["text"] for w in result["words"]] == ["good"]
    assert result["words"][0]["start"] == 4
    assert any("bounding box" in w for w in warnings)


def test_mismatch_does_not_search_ahead_for_another_identical_word():
    result, warnings = reading([row("one"), row("two")], "one omitted two")
    assert [w["text"] for w in result["words"]] == ["one"]
    assert any("alignment failed" in w for w in warnings)


def test_malformed_row_does_not_shift_repeated_word_geometry():
    result, warnings = reading(["5\tbroken", row("same")], "same same")
    assert result["words"] == []
    assert warnings


def test_empty_ocr_and_nonword_rows():
    result, warnings = reading(["1\t1\t0\t0\t0\t0\t0\t0\t800\t600\t-1\t"], "")
    assert result["words"] == []
    assert not warnings


def test_native_pdf_keeps_text_unreviewed():
    from pypdf import PdfReader
    from reportlab.pdfgen.canvas import Canvas

    stream = io.BytesIO()
    canvas = Canvas(stream)
    canvas.drawString(50, 700, "Native reading is unreviewed.")
    canvas.save()
    data = stream.getvalue()
    expected = PdfReader(io.BytesIO(data)).pages[0].extract_text(extraction_mode="layout")
    result = parse(data, ".pdf", {**DEFAULTS, "ocr": "off"})
    section = result["sections"][0]
    assert section["text"] == expected
    assert section["locator"] == {"kind": "pdf", "page": 1, "reading": native_reading()}
    assert section["locator"]["reading"]["review_status"] == "automatic_unreviewed"


@pytest.fixture
def ocr_config():
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract is required for actual OCR verification")
    languages = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, check=True)
    installed = languages.stdout.splitlines()
    # Either Latin-script model exercises the actual engine; production config is unchanged.
    language = next((lang for lang in ("eng", "afr") if lang in installed), None)
    if language is None:
        pytest.skip("Tesseract eng or afr language data required for Latin-script test fixture")
    return {**DEFAULTS, "ocr": "auto", "ocr_languages": language}


@pytest.fixture
def ocr_image(ocr_config):
    image = Image.new("RGB", (1000, 220), "white")
    ImageDraw.Draw(image).text(
        (40, 60), "Evidence reading 12345", font=ImageFont.load_default(size=48), fill="black"
    )
    return image


def assert_ocr(result, kind, index, artifact_name, language):
    assert result["sections"], result["warnings"]
    section = result["sections"][0]
    assert "Evidence reading 12345" in section["text"]
    locator = section["locator"]
    assert locator["kind"] == kind
    assert locator["page" if kind == "pdf" else "frame"] == index
    provenance = locator["reading"]
    assert provenance["engine"] == "tesseract"
    assert provenance["language"] == language
    assert provenance["review_status"] == "automatic_unreviewed"
    assert provenance["words"]
    for word in provenance["words"]:
        assert section["text"][word["start"] : word["end"]] == word["text"]
    artifacts = {a["name"]: a["data"] for a in result["artifacts"]}
    assert artifacts[artifact_name + ".png"].startswith(b"\x89PNG")
    assert artifacts[artifact_name + ".ocr.txt"].decode() == section["text"]
    assert json.loads(artifacts[artifact_name + ".ocr.json"])["reading"] == provenance
    assert not any("provenance" in w or "alignment" in w for w in result["warnings"])


def test_real_image_ocr(ocr_image, ocr_config):
    stream = io.BytesIO()
    ocr_image.save(stream, format="PNG")
    result = parse(stream.getvalue(), ".png", ocr_config)
    assert_ocr(result, "image", 0, "image-0", ocr_config["ocr_languages"])
    assert result["sections"][0]["locator"]["reading"]["image_width"] == 1000


def test_real_scanned_pdf_ocr(ocr_image, ocr_config):
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm required for scanned PDF OCR verification")
    stream = io.BytesIO()
    ocr_image.save(stream, format="PDF")
    result = parse(stream.getvalue(), ".pdf", ocr_config)
    assert_ocr(result, "pdf", 1, "page-1", ocr_config["ocr_languages"])


def test_real_empty_image_ocr(ocr_image, ocr_config):
    stream = io.BytesIO()
    Image.new("RGB", ocr_image.size, "white").save(stream, format="PNG")
    result = parse(stream.getvalue(), ".png", ocr_config)
    assert result["sections"][0]["text"] == ""
    assert result["sections"][0]["locator"]["reading"]["words"] == []
    assert "OCR returned no readable text" in result["warnings"]


def test_real_multiple_frames_keep_source_association(ocr_image, ocr_config):
    stream = io.BytesIO()
    blank = Image.new("RGB", ocr_image.size, "white")
    blank.save(stream, format="TIFF", save_all=True, append_images=[ocr_image])
    result = parse(stream.getvalue(), ".tiff", ocr_config)
    assert [s["locator"]["frame"] for s in result["sections"]] == [0, 1]
    assert result["sections"][0]["locator"]["reading"]["words"] == []
    assert_ocr(
        {**result, "sections": result["sections"][1:]}, "image", 1, "image-1", ocr_config["ocr_languages"]
    )


def test_disabled_ocr_preserves_frame_artifact():
    stream = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    result = parse(stream.getvalue(), ".png", {**DEFAULTS, "ocr": "off"})
    assert result["sections"] == []
    assert any("OCR disabled" in warning for warning in result["warnings"])
    assert any(a["name"] == "image-0.png" for a in result["artifacts"])


def test_missing_tsv_keeps_engine_text(tmp_path, monkeypatch):
    from evidencekg.parsers.worker import extract

    def without_tsv(args, **kwargs):
        assert args[-2:] == ["txt", "tsv"]
        Path(args[2] + ".txt").write_bytes("  Grüße\r\n".encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", without_tsv)
    stream = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    result = extract(stream.getvalue(), ".png", {**DEFAULTS, "ocr": "auto"}, tmp_path)
    assert result["sections"][0]["text"] == "  Grüße\r\n"
    assert result["sections"][0]["locator"]["reading"]["words"] == []
    assert any("TSV header" in warning for warning in result["warnings"])
