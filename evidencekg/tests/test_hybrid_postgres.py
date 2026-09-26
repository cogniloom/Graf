"""Real PostgreSQL tests, explicitly enabled with EVIDENCEKG_TEST_DSN.

The DSN must name a disposable test database whose owner may migrate. Tests use
unique identities and leave append-only fixtures in place, never reset a schema.
"""

from __future__ import annotations

import copy
import os
from uuid import uuid4

import pytest
from evidencekg.db import dump, ident, sha
from evidencekg.hybrid.postgres import PostgresWorkset, _snapshot_payload, migrate


@pytest.fixture
def dsn():
    value = os.environ.get("EVIDENCEKG_TEST_DSN")
    if not value:
        pytest.skip("EVIDENCEKG_TEST_DSN is not configured")
    pytest.importorskip("psycopg")
    migrate(value)
    return value


@pytest.fixture
def workset(dsn):
    store = PostgresWorkset(dsn)
    yield store
    store.close()


@pytest.fixture
def identity():
    return {"snapshot": uuid4().hex, "question": "Was steht im Original?", "model": "fixed"}


@pytest.fixture
def source():
    text = "Überprüfung\nOriginal\ttext 🦉"
    sid = ident("S", "extraction", 0, 0, len(text))
    manifest = {
        "documents": [{"document_version_id": "doc", "extraction_id": "extraction", "path": "a.pdf"}],
        "inventory_complete": True,
        "source_index_provenance_verified": False,
    }
    segments = {
        sid: {
            "id": sid,
            "document_version_id": "doc",
            "extraction_id": "extraction",
            "ordinal": 0,
            "char_start": 0,
            "char_end": len(text),
            "text": text,
            "text_sha": sha(text),
            "artifact_sha": sha("artifact"),
            "source_path": "a.pdf",
            "locators": [{"page": 2, "start": 0, "end": len(text), "label": "原文"}],
        }
    }
    links = [
        {
            "id": "link",
            "from_node": "doc",
            "to_node": None,
            "relation_type": "reference",
            "status": "unresolved",
            "rule_id": "rule",
            "rule_version": "1",
            "derivation_json": dump({"source": sid, "literal": "Überprüfung"}),
        }
    ]
    return sha(dump(manifest)), manifest, segments, links


def test_source_validation_preserves_provenance_and_rejects_text_drift(source):
    manifest_sha, manifest, segments, links = source
    payload = _snapshot_payload(*source)
    assert "原文" in payload
    assert '"source_index_provenance_verified":false' in payload
    changed = copy.deepcopy(segments)
    next(iter(changed.values()))["text"] += "!"
    with pytest.raises(ValueError, match="integrity"):
        _snapshot_payload(manifest_sha, manifest, changed, links)
    with pytest.raises(ValueError, match="hash mismatch"):
        _snapshot_payload("0" * 64, manifest, segments, links)
    with pytest.raises(ValueError, match="inventory gap"):
        _snapshot_payload(manifest_sha, manifest, {}, links)


def test_augmented_manifest_hash_binds_original_and_postings(source):
    manifest_sha, manifest, segments, links = source
    augmented = manifest | {"hybrid_manifest_sha": manifest_sha, "hybrid_typed_postings": [{"id": "p"}]}
    assert "hybrid_typed_postings" in _snapshot_payload(manifest_sha, augmented, segments, links)
    augmented["inventory_complete"] = False
    with pytest.raises(ValueError, match="hash mismatch"):
        _snapshot_payload(manifest_sha, augmented, segments, links)


def test_freeze_page_review_and_reconnect(workset, dsn, identity):
    rows = [{"segment_id": f"s{i}", "text": "Original\n🦉", "locators": [{"page": i}]} for i in range(3)]
    accounting = {"eligible": 3, "remaining": {"empty_text": 0}}
    key = workset.freeze(identity, rows, accounting)
    assert workset.freeze(identity, rows, accounting) == key
    first = workset.page(key, limit=2)
    assert first["total"] == 3 and first["accounting"] == accounting
    assert first["items"][0] == rows[0] | {"review_state": "pending"}
    assert [r["segment_id"] for r in workset.page(key, first["next_cursor"])["items"]] == ["s2"]
    workset.mark_reviewed(key, "s0", {"reviewer": "test", "evidence": "original"})
    workset.mark_reviewed(key, "s0", {"reviewer": "test", "evidence": "original"})
    with pytest.raises(ValueError, match="receipt drift"):
        workset.mark_reviewed(key, "s0", {"reviewer": "other"})
    with pytest.raises(ValueError, match="outside"):
        workset.mark_reviewed(key, "not-present", {"reviewer": "test"})
    with pytest.raises(ValueError, match="Explicit"):
        workset.mark_reviewed(key, "s0", {})
    with pytest.raises(ValueError, match="drift"):
        workset.freeze(identity, rows[::-1], accounting)
    with pytest.raises(ValueError, match="Duplicate"):
        workset.freeze(identity, rows + rows, accounting)
    other_key = workset.freeze(identity | {"question": "other"}, rows, accounting)
    with pytest.raises(ValueError, match="foreign cursor"):
        workset.page(other_key, first["next_cursor"])
    for bad_limit in (0, 1001, True, 1.5):
        with pytest.raises(ValueError, match="page size"):
            workset.page(key, limit=bad_limit)
    for bad_cursor in ("", "not base64", "e30="):
        with pytest.raises(ValueError, match="cursor"):
            workset.page(key, bad_cursor)
    reopened = PostgresWorkset(dsn)
    try:
        assert reopened.page(key)["items"][0]["review_state"] == "reviewed"
        assert len(reopened.page(key)["items"]) == 3
    finally:
        reopened.close()


def test_empty_workset(workset, identity):
    key = workset.freeze(identity, [], {"eligible": 0})
    assert workset.page(key) == {"items": [], "next_cursor": None, "total": 0, "accounting": {"eligible": 0}}


def test_snapshot_roundtrip_immutable_and_augmented(workset, source):
    manifest_sha, manifest, segments, links = source
    manifest = manifest | {"hybrid_manifest_sha": manifest_sha, "hybrid_typed_postings": [{"id": "p"}]}
    snapshot = uuid4().hex
    assert workset.import_snapshot(snapshot, manifest_sha, manifest, segments, links) == snapshot
    assert workset.load_snapshot(snapshot) == (manifest, segments, links)
    assert workset.import_snapshot(snapshot, manifest_sha, manifest, segments, links) == snapshot
    changed = manifest | {"hybrid_typed_postings": [{"id": "different"}]}
    with pytest.raises(ValueError, match="snapshot drift"):
        workset.import_snapshot(snapshot, manifest_sha, changed, segments, links)
    assert workset.load_snapshot(snapshot) == (manifest, segments, links)
    with pytest.raises(ValueError, match="Unknown snapshot"):
        workset.load_snapshot(uuid4().hex)


def test_snapshot_partial_import_rolls_back(workset, source):
    import psycopg

    manifest_sha, manifest, segments, links = source
    snapshot = uuid4().hex
    invalid_links = [links[0] | {"id": None}]
    with pytest.raises(psycopg.errors.NotNullViolation):
        workset.import_snapshot(snapshot, manifest_sha, manifest, segments, invalid_links)
    with pytest.raises(ValueError, match="Unknown snapshot"):
        workset.load_snapshot(snapshot)
    assert workset.import_snapshot(snapshot, *source) == snapshot


def test_cache_immutable_and_hash_bound(workset, identity):
    result = {"segments": [{"text": "Original\n🦉", "locators": [{"page": 1}]}], "accounting": {"all": 1}}
    assert workset.get_result(identity) is None
    key = workset.put_result(identity, result)
    assert key == sha(dump(identity))
    assert workset.put_result(identity, result) == key
    assert workset.get_result(identity) == result
    assert workset.get_result(identity | {"model": "changed"}) is None
    with pytest.raises(ValueError, match="result drift"):
        workset.put_result(identity, result | {"extra": True})
    corrupt_identity = identity | {"question": "corrupt"}
    workset.db.execute(
        "INSERT INTO public.hybrid_results VALUES (%s,%s,%s,%s)",
        (sha(dump(corrupt_identity)), dump(corrupt_identity), dump(result), "0" * 64),
    )
    with pytest.raises(ValueError, match="integrity"):
        workset.get_result(corrupt_identity)


def test_inventory_corruption_and_database_mutation_guard(workset, identity):
    import psycopg

    key = workset.freeze(identity, [{"segment_id": "s0"}], {})
    with pytest.raises(psycopg.errors.RaiseException, match="Immutable"):
        workset.db.execute("UPDATE public.hybrid_candidates SET payload='{}' WHERE run=%s", (key,))
    assert workset.page(key)["total"] == 1
    workset.db.execute(
        "INSERT INTO public.hybrid_candidates VALUES (%s,%s,%s,%s)",
        (key, "extra", 1, dump({"segment_id": "extra"})),
    )
    with pytest.raises(ValueError, match="inventory drift"):
        workset.page(key)
    with pytest.raises(ValueError, match="inventory drift"):
        workset.mark_reviewed(key, "s0", {"review": "receipt"})


def test_snapshot_extra_edge_detected(workset, source):
    snapshot = uuid4().hex
    workset.import_snapshot(snapshot, *source)
    edge = source[-1][0] | {"id": "injected"}
    workset.db.execute(
        "INSERT INTO public.hybrid_edges VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (snapshot, edge["id"], 1, edge["from_node"], edge["to_node"], edge["relation_type"], dump(edge)),
    )
    with pytest.raises(ValueError, match="integrity"):
        workset.load_snapshot(snapshot)


def test_query_lock_serializes_connections_and_rolls_back(workset, dsn, identity):
    import psycopg

    other = PostgresWorkset(dsn)
    try:
        other.db.execute("SET lock_timeout = '100ms'")
        with workset.query_lock(identity):
            workset.freeze(identity, [{"segment_id": "s0"}], {})
            workset.put_result(identity, {"result": "retained"})
            assert other.get_result(identity) is None
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with other.query_lock(identity):
                    pytest.fail("Concurrent connection acquired held query lock")
        with other.query_lock(identity):
            assert other.get_result(identity) == {"result": "retained"}
        failed = identity | {"question": "rollback"}
        with pytest.raises(RuntimeError, match="abort"):
            with workset.query_lock(failed):
                workset.freeze(failed, [{"segment_id": "s1"}], {})
                workset.put_result(failed, {"result": "partial"})
                raise RuntimeError("abort")
        assert other.get_result(failed) is None
        with pytest.raises(ValueError, match="Unknown workset"):
            other.page(sha(dump(failed)))
    finally:
        other.close()


def test_constructor_has_no_ddl(monkeypatch):
    import evidencekg.hybrid.postgres as module

    class Connection:
        def execute(self, *args, **kwargs):
            pytest.fail("Constructor must not execute SQL")

        def close(self):
            pass

    monkeypatch.setattr(module, "_connect", lambda dsn: Connection())
    module.PostgresWorkset("unused").close()
