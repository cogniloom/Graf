"""Attach transcription uncertainty to interpretations without truth scores."""

from .db import ident


def reference(document, section, start, end):
    locator = section.get("locator", {})
    if locator.get("kind") not in ("pdf", "image"):
        return None
    origin = locator.get("reading_section_start", section["start"])
    section_end = locator.get("reading_section_end", section["end"])
    metadata = locator.get("reading", {})
    words = [w for w in metadata.get("words", []) if origin + w["start"] < end and origin + w["end"] > start]
    # This is a triage signal, never a calibrated correctness threshold.
    flagged = [w for w in words if w.get("score") is None or w["score"] < 60]
    return dict(
        id=ident(
            "RD",
            document["document_version_id"],
            document["extraction_id"],
            origin,
            section_end,
            locator.get("kind"),
            locator.get("page"),
            locator.get("frame"),
        ),
        method=metadata.get("method", section.get("modality", "unknown")),
        page=locator.get("page"),
        frame=locator.get("frame"),
        review_status="automatic_unreviewed",
        calibrated_probability=None,
        calibration_status=metadata.get("calibration_status", "unavailable"),
        word_scores_available=bool(words),
        flagged_word_count=len(flagged),
        flagged_spans=[
            dict(start=origin + w["start"], end=origin + w["end"], score=w.get("score")) for w in flagged[:32]
        ],
        triage_policy="raw score below 60 or unavailable; not an accuracy probability",
        usage="candidate reading; verify important words against page image; source truth unknown",
    )


def compact_locator(locator):
    result = {k: v for k, v in locator.items() if k != "words"}
    if "reading" in result:
        result["reading"] = {k: v for k, v in result["reading"].items() if k != "words"}
        result["reading"]["word_count"] = len(locator["reading"].get("words", []))
    return result
