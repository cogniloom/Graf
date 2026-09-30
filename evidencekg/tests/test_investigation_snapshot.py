"""Regression for selective retention; retrieval/database boundaries are fixtures."""

from types import SimpleNamespace

import pytest

from evidencekg.app.manager import Manager


def test_unselected_large_original_is_not_copied(monkeypatch, tmp_path):
    import evidencekg.app.manager as module
    import evidencekg.hybrid.sources as sources

    publication = {
        "state": str(tmp_path / "generations" / "fixture"),
        "config": "fixture-config",
        "snapshot_id": "snapshot",
        "sources": [{"id": "source"}],
    }
    row = {"state": "ready", "revision": 1, "published_revision": 1, "publication": publication}

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            return self

        def fetchone(self):
            return None

    monkeypatch.setattr(module, "_connect", lambda *args: Database())
    monkeypatch.setattr(module, "restricted_hashes", lambda *args: set())
    monkeypatch.setattr(module, "restricted_paths", lambda *args: set())
    reads = []

    class Store:
        def __init__(self, *args):
            pass

        def rows(self, *args):
            return []

        def one(self, sql, args):
            if "document_versions" in sql:
                return {"original_blob_sha": args[0]}
            return {"byte_length": 9 if args[0] == "small" else 300 * 1024 * 1024}

        def get(self, key):
            reads.append(key)
            assert key == "small", "Unselected large source must not be read"
            return b"Small doc"

        def close(self):
            pass

    monkeypatch.setattr(sources, "ReadOnlyStore", Store)

    def load(*args):
        return (
            {
                "documents": [
                    {"document_version_id": name, "path": name + ".txt", "status": "ready"}
                    for name in ["small", "large"]
                ]
            },
            {name: {"id": name, "document_version_id": name, "text": name} for name in ["small", "large"]},
            [],
        )

    monkeypatch.setattr(sources, "load_sources", load)
    manager = Manager.__new__(Manager)
    manager.config = SimpleNamespace(home=tmp_path)
    manager.dsn = "fixture"
    manager.id = "fixture-workspace"
    manager.reconcile = lambda: True
    manager._row = lambda *args: row
    manager._sources = lambda *args: [{"id": "source", "enabled": True}]
    manager._paths = lambda value, *args: value
    manager.sources = lambda: {"items": [{"enabled": True, "file_count": 2}]}
    manager._with_runtime = lambda *args: {"segments": [{"segment_id": "small"}]}
    snapshot = manager.investigation_snapshot("Find small doc")
    assert reads == ["small"]
    assert snapshot["collection_documents"] == 2
    assert [d["document_version_id"] for d in snapshot["manifest"]["documents"]] == ["small"]
    assert set(snapshot["segments"]) == {"small"}
    assert snapshot["selected_segments"] == ["small"]
    manager._with_runtime = lambda *args: {"segments": [{"segment_id": "large"}]}
    with pytest.raises(ValueError, match="256 MiB"):
        manager.investigation_snapshot("Find large doc")
