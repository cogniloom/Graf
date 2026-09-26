import pytest

from evidencekg.benchmark import retrieve
from evidencekg.ingest import ingest
from evidencekg.reference_cache import ReferencePageCache
from evidencekg.retrieval import API


def test_original_pages_and_full_ranking_identical(vault):
    root, store = vault
    for i in range(24):
        (root / f"{i}.txt").write_text(
            f"Case: 123\n\nShared original paragraph with precise wording.\n\nUnique marker{i}; deadline Friday, not Monday."
        )
    snapshot = ingest(store)
    native = API(store)
    cached = ReferencePageCache(store)
    doc = store.manifest(snapshot)["documents"][0]["document_version_id"]
    cursor = None
    page = 0
    while True:
        size = [1, 7, 13, 100][page % 4]
        expected = native.neighbours(snapshot, doc, cursor=cursor, limit=size)
        assert cached.neighbours(snapshot, doc, cursor=cursor, limit=size) == expected
        cursor = expected["next_cursor"]
        page += 1
        if cursor is None:
            break
    assert cached.materializations == 1 and cached.cache_hits == page - 1
    for question in ["When is marker4 due?", "What is not Monday?", "unmatched term"]:
        for mode in ["baseline", "graph"]:
            assert retrieve(ReferencePageCache(store), snapshot, question, mode, limit=12) == retrieve(
                native, snapshot, question, mode, limit=12
            )
    filtered = native.neighbours(snapshot, doc, relation_types=["SHARED_PARAGRAPH"])
    assert cached.neighbours(snapshot, doc, relation_types=["SHARED_PARAGRAPH"]) == filtered
    assert cached.materializations == 2


def test_snapshot_and_cursor_scope_stay_bound(vault):
    root, store = vault
    (root / "a.txt").write_text("Shared paragraph.")
    (root / "b.txt").write_text("Shared paragraph.")
    one = ingest(store)
    native = API(store)
    cached = ReferencePageCache(store)
    doc = store.manifest(one)["documents"][0]["document_version_id"]
    first = cached.neighbours(one, doc, limit=1)
    (root / "c.txt").write_text("Shared paragraph.")
    two = ingest(store)
    assert cached.neighbours(two, doc) == native.neighbours(two, doc)
    with pytest.raises(ValueError, match="Invalid cursor"):
        cached.neighbours(two, doc, cursor=first["next_cursor"])
    assert cached.neighbours(one, doc) == native.neighbours(one, doc)


def test_cached_byte_bound_and_caller_mutation_match_original(vault):
    root, store = vault
    (root / "a.txt").write_text("Original.")
    snapshot = ingest(store)
    native, cached = API(store), ReferencePageCache(store)
    scope = {"kind": "neighbours", "node": "synthetic", "relations": None}
    rows = [{"id": str(i), "value": "ü" * 160000} for i in range(3)]
    cached._request = "synthetic"
    first = cached.page(snapshot, scope, rows, limit=3)
    assert first == native.page(snapshot, scope, rows, limit=3)
    assert len(first["items"]) == 2 and first["remaining"] == 1
    first["items"][0]["value"] = "mutated returned object"
    assert cached._page(None, 3) == native.page(snapshot, scope, rows, limit=3)
    assert cached._page(first["next_cursor"], 1) == native.page(
        snapshot, scope, rows, first["next_cursor"], 1
    )
    with pytest.raises(ValueError, match="response budget"):
        cached.page(snapshot, scope, [{"id": "x", "value": "z" * 750001}])
