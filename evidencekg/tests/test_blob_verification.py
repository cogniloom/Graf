import tracemalloc

import pytest

from evidencekg.db import sha
from evidencekg.features import index_extraction
from evidencekg.ingest import ingest


def test_blob_verification_has_bounded_memory_and_rejects_corruption(vault):
    _, store = vault
    key = store.put(b"x" * (8 * 1024 * 1024))
    tracemalloc.start()
    try:
        store.verify_blob(key)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024
    path = store.state / "objects" / key[:2] / key
    with path.open("r+b") as stream:
        stream.write(b"y")
    with pytest.raises(ValueError, match="Corrupt artifact"):
        store.verify_blob(key)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        store.verify_blob(key)


@pytest.mark.parametrize("key", [None, "../escape", "A" * 64, "0" * 63])
def test_blob_verification_rejects_invalid_keys(vault, key):
    with pytest.raises(ValueError, match="Invalid artifact key"):
        vault[1].verify_blob(key)


def test_ingestion_indexes_identity_without_loading_blob_again(vault, monkeypatch):
    root, store = vault
    (root / "source.txt").write_text("Captured source.")
    original_get = store.get

    def get_metadata_only(key):
        assert key != source_key, "Identity indexing must not allocate the captured file again"
        return original_get(key)

    source_key = sha(b"Captured source.")
    monkeypatch.setattr(store, "get", get_metadata_only)
    snapshot = ingest(store)
    document = store.manifest(snapshot)["documents"][0]
    assert store.one("SELECT canonical_value FROM features WHERE kind='original_blob'")["canonical_value"] == source_key
    path = store.state / "objects" / source_key[:2] / source_key
    path.write_bytes(b"altered")
    with pytest.raises(ValueError, match="Corrupt artifact"):
        index_extraction(store, document["extraction_id"], "Captured source.", store.config(), source_key)
