"""Immutable extraction readings and append-only human review overlays.

A reviewed transcription is not a verified event. Corrections never overwrite
canonical evidence or silently inherit the original claim's source offsets.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .db import dump, ident, sha
from .knowledge_storage import read as read_knowledge


class ReviewConflict(ValueError):
    pass


def reading_id(document, section):
    locator = section.get("locator", {})
    return ident(
        "RD",
        document["document_version_id"],
        document["extraction_id"],
        section["start"],
        section["end"],
        locator.get("kind"),
        locator.get("page"),
        locator.get("frame"),
    )


def readings(store, snapshot, document_id):
    document = next(
        (d for d in store.manifest(snapshot)["documents"] if d["document_version_id"] == document_id), None
    )
    if document is None:
        raise KeyError("Unknown current document")
    extraction = store.one("SELECT * FROM extractions WHERE id=?", (document["extraction_id"],))
    artifact = json.loads(store.get(extraction["artifact_sha"]))
    knowledge = read_knowledge(store, document["knowledge"]["blob"]) if document.get("knowledge") else {}
    result = []
    for section in artifact["locators"]:
        locator = section.get("locator", {})
        if locator.get("kind") not in ("pdf", "image"):
            continue
        start, end = section["start"], section["end"]
        metadata = locator.get("reading", {})
        result.append(
            dict(
                id=reading_id(document, section),
                document_id=document_id,
                extraction_id=document["extraction_id"],
                artifact_sha=extraction["artifact_sha"],
                original_blob_sha=document.get("blob"),
                snapshot_id=snapshot,
                start=start,
                end=end,
                text=artifact["text"][start:end],
                page=locator.get("page"),
                frame=locator.get("frame"),
                method=metadata.get("method", section.get("modality", "unknown")),
                metadata=metadata,
                words=metadata.get("words", []),
                review_status="automatic_unreviewed",
                truth_status="unknown",
                claims=[
                    c
                    for c in knowledge.get("claims", [])
                    if start <= c["canonical_start"] and c["canonical_end"] <= end
                ],
                warnings=document.get("warnings", []),
            )
        )
    return document, result


class ReviewLedger:
    """Private local overlay, retained across publications; no source text updates."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.path.chmod(0o600)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reading_reviews(
                    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
                    reading_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    payload TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS reading_review_target ON reading_reviews(reading_id,target_id,seq);
                CREATE TRIGGER IF NOT EXISTS reading_review_no_update BEFORE UPDATE ON reading_reviews
                    BEGIN SELECT RAISE(ABORT, 'append only reviews'); END;
                CREATE TRIGGER IF NOT EXISTS reading_review_no_delete BEFORE DELETE ON reading_reviews
                    BEGIN SELECT RAISE(ABORT, 'append only reviews'); END;
            """)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def history(self, db, reading):
        return [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM reading_reviews WHERE reading_id=? ORDER BY seq", (reading["id"],)
            )
        ]

    def decorate(self, db, reading):
        history = self.history(db, reading)
        source = next((r for r in reversed(history) if r["target_kind"] == "reading"), None)
        source_revision = source["id"] if source else None
        result = dict(reading, history=history, review=source, review_revision=source_revision)
        result["review_status"] = source["decision"] if source else "automatic_unreviewed"
        result["effective_text"] = (
            source["correction"] if source and source["decision"] == "corrected" else reading["text"]
        )
        result["claims"] = []
        for claim in reading["claims"]:
            review = next(
                (
                    r
                    for r in reversed(history)
                    if r["target_kind"] == "claim" and r["target_id"] == claim["id"]
                ),
                None,
            )
            changed = source and source["decision"] in ("corrected", "unreadable")
            stale = review and review["reading_revision"] != source_revision
            result["claims"].append(
                dict(
                    claim,
                    reading_review_status=result["review_status"],
                    interpretation_review=review,
                    review_revision=review["id"] if review else None,
                    review_status="needs_reassessment"
                    if stale
                    else review["decision"]
                    if review
                    else "needs_reassessment"
                    if changed
                    else "automatic_unreviewed",
                    truth_status="unknown",
                )
            )
        result["correction_candidates"] = []
        if source and source["decision"] == "corrected":
            from .knowledge import clauses, parse_clause

            for lo, hi, quote in clauses(source["correction"]):
                parsed = parse_clause(quote)
                if parsed:
                    candidate_id = ident("KC", source_revision, lo, hi)
                    review = next(
                        (
                            r
                            for r in reversed(history)
                            if r["target_kind"] == "claim" and r["target_id"] == candidate_id
                        ),
                        None,
                    )
                    result["correction_candidates"].append(
                        dict(
                            **parsed,
                            id=candidate_id,
                            kind="corrected_claim",
                            quote=quote,
                            correction_start=lo,
                            correction_end=hi,
                            reading_revision=source_revision,
                            review_status=review["decision"] if review else "automatic_unreviewed",
                            review_revision=review["id"] if review else None,
                            interpretation_review=review,
                            usage="candidate from human correction; not an original quotation",
                            truth_status="unknown",
                        )
                    )
        return result

    def append(
        self,
        db,
        reading,
        *,
        target_kind,
        target_id,
        decision,
        reason,
        reviewer,
        correction="",
        expected_revision=None,
        reading_revision=None,
        source_checked=False,
    ):
        allowed = {
            "reading": {"confirmed", "corrected", "unreadable"},
            "claim": {"accepted", "rejected", "unresolved"},
        }
        if target_kind not in allowed or decision not in allowed[target_kind]:
            raise ValueError("Unsupported review decision")
        if source_checked is not True:
            raise ValueError("Compare the reading with the original page before saving a review")
        if not reason.strip() or not reviewer.strip():
            raise ValueError("Reviewer name and reason are required")
        if len(reason) > 4000 or len(reviewer) > 200 or len(correction) > 16000:
            raise ValueError("Review exceeds size limit")
        if (decision == "corrected") != bool(correction.strip()):
            raise ValueError("Only a correction decision requires replacement text")
        if decision == "corrected" and correction == reading["text"]:
            raise ValueError("Use Confirm for an unchanged reading")
        current = self.decorate(db, reading)
        if target_kind == "reading":
            if target_id != reading["id"]:
                raise ValueError("Reading target mismatch")
            previous = current["review"]
            if decision == "confirmed" and not reading["text"].strip():
                raise ValueError("An empty reading cannot be confirmed")
        else:
            claim = next(
                (c for c in current["claims"] + current["correction_candidates"] if c["id"] == target_id),
                None,
            )
            if claim is None:
                raise ValueError("Claim does not belong to this reading")
            previous = claim["interpretation_review"]
            if reading_revision != current["review_revision"]:
                raise ReviewConflict("Reading changed; reload before reviewing the claim")
            corrected_candidate = (
                claim.get("kind") == "corrected_claim" and current["review_status"] == "corrected"
            )
            if decision == "accepted" and current["review_status"] != "confirmed" and not corrected_candidate:
                raise ValueError("Confirm the original reading before accepting its interpretation")
        if expected_revision != (previous["id"] if previous else None):
            raise ReviewConflict("A newer review exists; reload before saving")
        record = dict(
            target_kind=target_kind,
            target_id=target_id,
            decision=decision,
            reason=reason.strip(),
            reviewer=reviewer.strip(),
            reviewer_identity="self_reported_local_reviewer",
            correction=correction,
            supersedes=expected_revision,
            reading_revision=reading_revision,
            reading_id=reading["id"],
            reading_start=reading["start"],
            reading_end=reading["end"],
            page=reading.get("page"),
            frame=reading.get("frame"),
            document_id=reading["document_id"],
            extraction_id=reading["extraction_id"],
            artifact_sha=reading["artifact_sha"],
            original_blob_sha=reading["original_blob_sha"],
            snapshot_id=reading["snapshot_id"],
            created_at=time.time(),
            truth_status="unknown",
            source_checked=True,
            target_quote=claim["quote"] if target_kind == "claim" else None,
            quote_origin="corrected_reading"
            if target_kind == "claim" and claim.get("kind") == "corrected_claim"
            else "original_extraction",
        )
        record["id"] = ident("RR", record)
        last = db.execute("SELECT hash FROM reading_reviews ORDER BY seq DESC LIMIT 1").fetchone()
        previous_hash = last[0] if last else "0" * 64
        payload = dump(record)
        db.execute(
            "INSERT INTO reading_reviews(id,reading_id,target_id,payload,previous_hash,hash) VALUES(?,?,?,?,?,?)",
            (
                record["id"],
                reading["id"],
                target_id,
                payload,
                previous_hash,
                sha(dump([previous_hash, payload])),
            ),
        )
        return record


def attach_reviews(home, segments):
    """Retain review evidence in newly captured research snapshots.

    Original segment text and citations stay byte-for-byte unchanged. Consumers
    receive explicit correction records and must reassess affected interpretations.
    """
    path = Path(home) / "source-reading-reviews.sqlite3"
    if not path.exists():
        return
    ledger = ReviewLedger(path)
    with ledger.transaction() as db:
        for segment in segments.values():
            records = [
                record
                for row in db.execute(
                    "SELECT payload FROM reading_reviews WHERE json_extract(payload,'$.extraction_id')=? ORDER BY seq",
                    (segment["extraction_id"],),
                )
                if (record := json.loads(row[0]))
                and (
                    "reading_start" not in record
                    or (
                        record["reading_start"] < segment["char_end"]
                        and record["reading_end"] > segment["char_start"]
                    )
                )
            ]
            if records:
                segment["source_reading_reviews"] = dict(
                    records=records,
                    usage="Original text is unchanged. Apply the latest review per target; corrected or unreadable readings require reassessment. A reviewed reading does not establish truth.",
                )


def overlay_knowledge(home, store, snapshot, response):
    ledger = ReviewLedger(Path(home) / "source-reading-reviews.sqlite3")
    if not ledger.path.exists():
        return response
    by_claim = {}
    docs = {item.get("document_version_id") for item in response.get("items", [])}
    with ledger.transaction() as db:
        for document_id in docs - {None}:
            _, items = readings(store, snapshot, document_id)
            for item in items:
                for claim in ledger.decorate(db, item)["claims"]:
                    by_claim[claim["id"]] = claim
    for item in response.get("items", []):
        if item.get("id") in by_claim:
            reviewed = by_claim[item["id"]]
            for key in (
                "review_status",
                "reading_review_status",
                "interpretation_review",
                "review_revision",
                "truth_status",
            ):
                item[key] = reviewed[key]
    return response
