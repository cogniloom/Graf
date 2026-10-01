"""Real vault checks for bounded preparation caches, without model inference."""

import json
import sys
import tracemalloc
import weakref
from types import SimpleNamespace

import pytest

from evidencekg.db import dump, ident, sha
from evidencekg.discovery_index import _sources
from evidencekg.knowledge_readings import compact_locator
from evidencekg.relationships import verify_posting


def snapshot(store, texts, padding=0, locator=None, compact=False):
    """Retain canonical artifacts/segments directly; no parser or model calls."""
    documents, postings = [], []
    corpus = store.one("SELECT id FROM corpora")["id"]
    with store.write():
        for i, text in enumerate(texts):
            path = f"source-{i}.txt"
            entry, document, extraction = (ident(prefix, i) for prefix in ("E", "D", "X"))
            blob = store.put(text.encode())
            locators = [dict(start=0, end=len(text), locator=locator or dict(kind="fixture", source=path))]
            segment_locators = (
                [dict(loc, locator=compact_locator(loc["locator"])) for loc in locators]
                if compact
                else locators
            )
            artifact = store.put(dump(dict(text=text, locators=locators, audit_padding="z" * padding)))
            store.db.execute("INSERT INTO source_entries VALUES(?,?,?,?,?)", (entry, corpus, path, 0, path))
            store.db.execute(
                "INSERT INTO document_versions VALUES(?,?,?,?,?,?,?,?)",
                (document, entry, blob, 0, None, None, "text/plain", None),
            )
            store.db.execute(
                "INSERT INTO extractions VALUES(?,?,?,?,?,?,?,?)",
                (extraction, document, "fixture", "fixture", "fixture", artifact, "ready", "[]"),
            )
            width = max(1, len(text) // 3)
            starts = list(range(0, len(text), width)) or [0]
            for ordinal, start in enumerate(starts):
                end = min(start + width, len(text))
                segment = ident("S", extraction, ordinal, start, end)
                body = text[start:end]
                store.db.execute(
                    "INSERT INTO segments VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        segment,
                        extraction,
                        ordinal,
                        sha(body),
                        start,
                        end,
                        dump(segment_locators),
                        "text",
                        "ready",
                        body,
                    ),
                )
                if ordinal == 0:
                    postings.append(
                        dict(
                            kind="original_blob",
                            segment_id=segment,
                            start=0,
                            end=min(1, len(body)),
                            raw_value=body[:1],
                            canonical_value=blob,
                            ambiguity_json=dump(dict(canonical_start=0, canonical_end=len(text))),
                        )
                    )
            documents.append(
                dict(
                    path=path,
                    document_version_id=document,
                    extraction_id=extraction,
                    blob=blob,
                    artifact_sha=artifact,
                )
            )
        manifest = dict(documents=documents)
        manifest_sha = store.put(dump(manifest))
        sid = ident("N", manifest_sha)
        store.db.execute("INSERT INTO snapshots VALUES(?,?,?,?)", (sid, manifest_sha, "fixture", 0))
        store.db.executemany(
            "INSERT INTO snapshot_documents VALUES(?,?,?)",
            [(sid, d["document_version_id"], d["extraction_id"]) for d in documents],
        )
    return sid, documents, postings


def test_source_validation_releases_decoded_artifacts(vault):
    store = vault[1]
    texts = [f"Document {i}: all original characters remain." for i in range(12)] + [""]
    sid, documents, _ = snapshot(store, texts, padding=1024 * 1024)
    tracemalloc.start()
    try:
        result = _sources(store, sid)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 6 * 1024 * 1024
    assert result["manifest"]["documents"] == documents
    segments = result["segments"]
    assert [s["id"] for s in segments] == sorted(s["id"] for s in segments)
    for doc, text in zip(documents, texts, strict=True):
        parts = sorted(
            (s for s in segments if s["extraction_id"] == doc["extraction_id"]),
            key=lambda s: s["ordinal"],
        )
        assert parts and "".join(s["text"] for s in parts) == text
        assert all(s["artifact_sha"] == doc["artifact_sha"] for s in parts)
        assert all(s["document_version_id"] == doc["document_version_id"] for s in parts)
        assert all(
            s["locators"]
            == [
                dict(
                    start=0,
                    end=len(text),
                    locator=dict(kind="fixture", source=doc["path"]),
                )
            ]
            for s in parts
        )


def test_original_verification_streams_and_caches_only_markers(vault, monkeypatch):
    store = vault[1]
    _, _, rows = snapshot(store, ["x" * (8 * 1024 * 1024)])
    original_verify = store.verify_blob
    verified = []

    def verify(key):
        verified.append(key)
        original_verify(key)

    def forbid_get(key):
        pytest.fail("Original byte verification must not load an entire blob")

    monkeypatch.setattr(store, "verify_blob", verify)
    monkeypatch.setattr(store, "get", forbid_get)
    cache = {}
    verify_posting(store, rows[0], cache)
    verify_posting(store, rows[0], cache)
    assert verified == [rows[0]["canonical_value"]]
    assert cache == {("verified_blob", rows[0]["canonical_value"]): True}
    key = rows[0]["canonical_value"]
    with (store.state / "objects" / key[:2] / key).open("r+b") as stream:
        stream.write(b"y")
    fresh = {}
    with pytest.raises(ValueError, match="Corrupt artifact"):
        verify_posting(store, rows[0], fresh)
    assert not fresh


def test_paragraph_cache_keeps_only_current_extraction(vault):
    store = vault[1]
    texts = [f"Distinct paragraph {i}. " * 100 for i in range(12)]
    _, documents, originals = snapshot(store, texts)
    cache = {}
    for doc, row, text in zip(documents, originals, texts, strict=True):
        verify_posting(store, row, cache)
        paragraph = dict(row, kind="paragraph", canonical_value=" ".join(text.split()))
        verify_posting(store, paragraph, cache)
        assert cache["paragraph_extraction"] == doc["extraction_id"]
        assert cache["paragraph_text"] == text
        assert all(value is True for key, value in cache.items() if isinstance(key, tuple))
        assert sum(isinstance(value, str) for value in cache.values()) == 2
    # Revisiting an evicted extraction rechecks its content hash.
    key = documents[0]["artifact_sha"]
    (store.state / "objects" / key[:2] / key).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Corrupt artifact"):
        verify_posting(store, dict(originals[0], kind="paragraph"), cache)
    assert "paragraph_text" not in cache
    assert "paragraph_extraction" not in cache


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("text", "Immutable source text/range/identity mismatch"),
        ("locator", "Original locator mismatch"),
        ("tail", "Immutable extraction tail gap"),
        ("gap", "Immutable segment coverage gap or overlap"),
        ("missing", "source inventory gap"),
        ("artifact", "Corrupt artifact"),
        ("membership", "Snapshot source membership mismatch"),
    ],
)
def test_source_validation_preserves_corruption_failures(vault, damage, reason):
    store = vault[1]
    sid, documents, _ = snapshot(store, ["Complete original paragraph.", "Second source."])
    document = documents[0]
    extraction = document["extraction_id"]
    first = store.one("SELECT id FROM segments WHERE extraction_id=? ORDER BY ordinal", (extraction,))["id"]
    if damage == "artifact":
        key = document["artifact_sha"]
        (store.state / "objects" / key[:2] / key).write_bytes(b"changed")
    else:
        with store.write():
            if damage == "text":
                store.db.execute("UPDATE segments SET text='changed' WHERE id=?", (first,))
            elif damage == "locator":
                store.db.execute("UPDATE segments SET locator_json='[{}]' WHERE id=?", (first,))
            elif damage == "tail":
                store.db.execute(
                    "DELETE FROM segments WHERE id=(SELECT id FROM segments WHERE extraction_id=? "
                    "ORDER BY ordinal DESC LIMIT 1)",
                    (extraction,),
                )
            elif damage == "missing":
                store.db.execute("DELETE FROM segments WHERE extraction_id=?", (extraction,))
            elif damage == "gap":
                store.db.execute("DELETE FROM segments WHERE extraction_id=? AND ordinal=1", (extraction,))
            else:
                manifest = store.manifest(sid)
                manifest["documents"][0]["document_version_id"] = "different-document"
                key = store.put(dump(manifest))
                store.db.execute("UPDATE snapshots SET manifest_sha=? WHERE id=?", (key, sid))
    with pytest.raises(ValueError, match=reason):
        _sources(store, sid)


def test_posting_occurrence_and_original_membership_still_checked(vault):
    store = vault[1]
    _, _, rows = snapshot(store, ["Original"])
    with pytest.raises(ValueError, match="Corrupt occurrence"):
        verify_posting(store, dict(rows[0], raw_value="changed"), {})
    with pytest.raises(ValueError, match="Invalid captured-byte feature"):
        verify_posting(store, dict(rows[0], canonical_value="0" * 64), {})
    with pytest.raises(ValueError, match="Paragraph hash hit failed"):
        verify_posting(store, dict(rows[0], kind="paragraph", canonical_value="different"), {})


@pytest.mark.parametrize("compact", [False, True])
def test_ocr_locator_projection_preserves_provenance_and_rejects_tampering(vault, compact):
    store = vault[1]
    locator = dict(
        kind="pdf",
        page=2,
        words=[dict(text="Original", bbox=[10, 20, 30, 40])],
        reading=dict(
            method="tesseract",
            calibration_status="unavailable",
            words=[dict(text="Original", start=0, end=8, bbox=[10, 20, 30, 40], score=42)],
        ),
    )
    sid, documents, _ = snapshot(store, ["Original OCR text."], locator=locator, compact=compact)
    verified = _sources(store, sid)
    parts = verified["segments"]
    assert parts
    for part in parts:
        retained = part["locators"][0]["locator"]
        assert retained["page"] == 2
        assert retained["reading"]["method"] == "tesseract"
        if compact:
            assert "words" not in retained and "words" not in retained["reading"]
            assert retained["reading"]["word_count"] == 1
        else:
            assert retained == locator
    assert json.loads(store.get(documents[0]["artifact_sha"]))["locators"][0]["locator"] == locator
    first = parts[0]
    for field in ("page", "word_count" if compact else "word_score"):
        tampered = json.loads(dump(first["locators"]))
        reading = tampered[0]["locator"]
        if field == "page":
            reading["page"] += 1
        elif field == "word_count":
            reading["reading"]["word_count"] += 1
        else:
            reading["reading"]["words"][0]["score"] += 1
        with store.write():
            store.db.execute("UPDATE segments SET locator_json=? WHERE id=?", (dump(tampered), first["id"]))
        with pytest.raises(ValueError, match="Original locator mismatch"):
            _sources(store, sid)


def test_prepare_releases_validation_inputs_before_model_loading(tmp_path, monkeypatch):
    """Exercise the phase boundary with external persistence/model work stopped."""
    from evidencekg.hybrid import graph, runtime

    def no_model(*args, **kwargs):
        pytest.fail("No model or index work")

    # This boundary test runs without optional NumPy/transformer packages.
    monkeypatch.setitem(
        sys.modules,
        "evidencekg.hybrid.semantic",
        SimpleNamespace(
            DenseAdapter=SimpleNamespace(from_local=no_model),
            RERANKER_MODEL_ID="unused",
            snapshot_identity=no_model,
        ),
    )
    monkeypatch.setitem(sys.modules, "evidencekg.hybrid.index", SimpleNamespace(DenseIndex=no_model))

    class RetainedList(list):
        pass

    class CacheValue:
        pass

    class ModelBoundary(Exception):
        pass

    class Store:
        closed = False

        def snapshot(self, value):
            return {"id": "snapshot", "manifest_sha": "binding"}

        def close(self):
            self.closed = True

    class Ledger:
        closed = False

        def import_snapshot(self, sid, digest, manifest, segments, links):
            assert (sid, digest) == ("snapshot", "binding")
            assert [row["id"] for row in manifest["hybrid_typed_postings"]] == ["a", "b", "c"]
            assert verified == ["b", "a", "c"]
            assert segments and links

        def close(self):
            self.closed = True

    retained = []
    verified = []
    store, ledger = Store(), Ledger()

    def tracked(values):
        result = RetainedList(values)
        retained.append(weakref.ref(result))
        return result

    def sources(*args):
        return (
            {"documents": tracked([{"document_version_id": "document"}])},
            {"segment": {"document_version_id": "document", "text": "source"}},
            tracked([{"id": "link"}]),
        )

    def verify(store, posting, cache):
        verified.append(posting["id"])
        value = CacheValue()
        retained.append(weakref.ref(value))
        cache["text"] = value

    def progress(**counts):
        if counts["indexing_stage"] == "loading_model":
            assert retained and all(reference() is None for reference in retained)
            raise ModelBoundary

    monkeypatch.setattr(runtime, "ReadOnlyStore", lambda path: store)
    monkeypatch.setattr(runtime, "PostgresWorkset", lambda dsn: ledger)
    monkeypatch.setattr(runtime, "database_dsn", lambda path: "unused")
    monkeypatch.setattr(runtime, "load_sources", sources)
    monkeypatch.setattr(runtime, "postings", lambda *args: tracked([
        {"id": "a", "extraction_id": "Y"},
        {"id": "b", "extraction_id": "X"},
        {"id": "c", "extraction_id": "Y"},
    ]))
    monkeypatch.setattr(runtime, "verify_posting", verify)
    monkeypatch.setattr(graph, "prepare_graph", lambda *args: {})
    with pytest.raises(ModelBoundary):
        runtime.prepare(tmp_path, tmp_path, tmp_path, device="cpu", progress=progress)
    assert store.closed and ledger.closed


def test_blob_verification_cannot_collide_with_paragraph_cache(vault):
    store = vault[1]
    _, documents, rows = snapshot(store, ["source"])
    store.db.execute("INSERT INTO blobs VALUES(?,?,?)", ("paragraph_text", 0, "invalid"))
    store.db.execute(
        "UPDATE document_versions SET original_blob_sha=? WHERE id=?",
        ("paragraph_text", documents[0]["document_version_id"]),
    )
    forged = dict(rows[0], canonical_value="paragraph_text")
    with pytest.raises(ValueError, match="Invalid artifact key"):
        verify_posting(store, forged, {"paragraph_text": "cached paragraph"})
