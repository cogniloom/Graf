"""Authenticated source-review routes using the existing publication fence."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from evidencekg.hybrid.sources import ReadOnlyStore
from evidencekg.source_readings import ReviewConflict, ReviewLedger, readings

from .manager import Missing, NotReady


class ReadingReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(min_length=1, max_length=200)
    target_kind: str = Field(max_length=20)
    target_id: str = Field(min_length=1, max_length=200)
    decision: str = Field(max_length=20)
    reason: str = Field(min_length=1, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=200)
    correction: str = Field(default="", max_length=16000)
    expected_revision: str | None = Field(default=None, max_length=200)
    reading_revision: str | None = Field(default=None, max_length=200)
    source_checked: StrictBool


def install(app, manager):
    ledger = ReviewLedger(manager.config.home / "source-reading-reviews.sqlite3")

    def source(row, document_id):
        store = ReadOnlyStore(row["publication"]["state"])
        try:
            document, items = readings(store, row["snapshot_id"], document_id)
            return store, document, items
        except KeyError as exc:
            store.close()
            raise Missing("Unknown current document") from exc
        except BaseException:
            store.close()
            raise

    def select(items, reading_id):
        item = next((r for r in items if r["id"] == reading_id), None)
        if item is None:
            raise Missing("Reading does not belong to this document")
        return item

    def require_snapshot(row, snapshot):
        if row["snapshot_id"] != snapshot:
            raise NotReady("Source publication changed; reload the reading")

    @app.exception_handler(ReviewConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/documents/{document_id}/readings")
    def document_readings(document_id: str, offset: int = Query(0, ge=0)):
        def perform(row):
            store, document, items = source(row, document_id)
            try:
                with ledger.transaction() as db:
                    item = ledger.decorate(db, items[offset]) if offset < len(items) else None
                return dict(snapshot_id=row["snapshot_id"], total=len(items), offset=offset, item=item)
            finally:
                store.close()

        return manager.evidence(perform)

    @app.post("/api/documents/{document_id}/readings/{reading_id}/reviews")
    def review(document_id: str, reading_id: str, body: ReadingReview):
        # Commit only after the post-operation publication fence succeeds.
        with ledger.transaction() as db:

            def perform(row):
                require_snapshot(row, body.snapshot_id)
                store, document, items = source(row, document_id)
                try:
                    item = select(items, reading_id)
                    ledger.append(db, item, **body.model_dump(exclude={"snapshot_id"}))
                    return ledger.decorate(db, item)
                finally:
                    store.close()

            return manager.evidence(perform)

    @app.get("/api/documents/{document_id}/readings/{reading_id}/image")
    def image(document_id: str, reading_id: str, snapshot_id: str = Query(max_length=200)):
        def perform(row):
            require_snapshot(row, snapshot_id)
            store, document, items = source(row, document_id)
            try:
                item = select(items, reading_id)
                name = f"page-{item['page']}.png" if item["page"] else f"image-{item['frame']}.png"
                art = next((a for a in document.get("artifacts", []) if a["name"] == name), None)
                if art:
                    return store.get(art["blob"])
                if not item["page"] or not document.get("blob"):
                    raise Missing("No page image is available for this reading")
                with tempfile.TemporaryDirectory(prefix="graf-page-review-") as temp:
                    directory = Path(temp)
                    pdf = directory / "source.pdf"
                    pdf.write_bytes(store.get(document["blob"]))
                    try:
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-m",
                                "evidencekg.reading_render",
                                str(pdf),
                                str(item["page"]),
                                str(directory / "page"),
                            ],
                            capture_output=True,
                            timeout=40,
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        raise ValueError("Page rendering unavailable; check local PDF renderer") from exc
                    if result.returncode or not (directory / "page.png").is_file():
                        raise ValueError("Page rendering failed; original document remains unchanged")
                    return (directory / "page.png").read_bytes()
            finally:
                store.close()

        return Response(
            manager.evidence(perform),
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
