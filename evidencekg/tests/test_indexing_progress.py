"""Local mechanics: progress boundaries, cancellation, cache, and device selection."""
import threading
from types import SimpleNamespace

import pytest

from evidencekg.db import sha
from evidencekg.hybrid.runtime import resolve_device


def test_auto_device_checks_usable_cuda_and_honors_explicit_cpu(monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    calls = []
    def allocation(*args, **kwargs):
        calls.append(kwargs["device"])
        return SimpleNamespace(sum=lambda: SimpleNamespace(item=lambda: 1))
    monkeypatch.setattr(torch, "ones", allocation)
    assert resolve_device("cpu") == "cpu"
    assert calls == []
    assert resolve_device("auto") == "cuda"
    assert calls == ["cuda"]
    def unavailable(*args, **kwargs):
        raise RuntimeError("driver cannot execute kernel")
    monkeypatch.setattr(torch, "ones", unavailable)
    assert resolve_device("auto") == "cpu"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert resolve_device("auto") == "cpu"
    with pytest.raises(ValueError):
        resolve_device("invalid")


def adapter():
    pytest.importorskip("torch")
    from test_hybrid_semantic import IDENTITY, ToyModel, ToyTokenizer

    from evidencekg.hybrid.semantic import DenseAdapter
    return DenseAdapter(ToyTokenizer(), ToyModel(), IDENTITY, max_tokens=4, batch_size=2)


def test_batch_counts_use_windows_and_preserve_vectors():
    np = pytest.importorskip("numpy")
    model = adapter()
    events = []
    texts = ["one two three four five", "six seven"]
    encoded = model.encode_passages(texts, progress=lambda **event: events.append(event))
    embedding = [e for e in events if e["indexing_stage"] == "embedding"]
    assert [e["indexing_completed"] for e in embedding] == [0, 2, 4]
    assert {e["indexing_total"] for e in embedding} == {4}
    assert events[0] == dict(indexing_stage="windowing", indexed_passages=0, total_passages=2)
    np.testing.assert_array_equal(encoded.vectors, model.encode_passages(texts).vectors)
    events.clear()
    model.encode_passages([], progress=lambda **event: events.append(event))
    assert events[-1] == dict(indexing_stage="embedding", indexing_completed=0, indexing_total=0)


def test_callback_failure_stops_batches_and_cannot_publish_index(tmp_path):
    model = adapter()
    from evidencekg.hybrid.index import DenseIndex
    def cancel(**event):
        if event.get("indexing_completed") == 2:
            raise InterruptedError("superseded")
    segments = {"s": {"text": "one two three four five six", "text_sha": sha("one two three four five six")}}
    with pytest.raises(InterruptedError):
        DenseIndex(tmp_path, model, segments, "snapshot", "manifest", progress=cancel)
    assert len(model.model.batches) == 1
    assert not (tmp_path / "index.json").exists()
    assert not (tmp_path / "vectors.npy").exists()


def test_cached_index_reports_verification_without_encoding(tmp_path):
    model = adapter()
    from evidencekg.hybrid.index import DenseIndex
    segments = {"s": {"text": "one two", "text_sha": sha("one two")}}
    events = []
    DenseIndex(tmp_path, model, segments, "snapshot", "manifest", progress=lambda **e: events.append(e))
    assert [e["indexing_stage"] for e in events][-2:] == ["saving_index", "verifying_index"]
    previous = len(model.model.batches)
    events.clear()
    DenseIndex(tmp_path, model, segments, "snapshot", "manifest", progress=lambda **e: events.append(e))
    assert len(model.model.batches) == previous
    assert [e["indexing_stage"] for e in events] == ["waiting_for_index", "verifying_index"]


def test_manager_preserves_extraction_and_partial_publication_and_throttles(tmp_path, monkeypatch):
    import evidencekg.config
    from evidencekg.app import ingestion
    from evidencekg.app import manager as module
    manager = module.Manager.__new__(module.Manager)
    manager.config = SimpleNamespace(home=tmp_path, models=tmp_path, database_config=tmp_path, device="auto", transcription_options=lambda: {})
    manager.dsn = "unused"
    manager.stop_event = threading.Event()
    manifest = {"documents": []}
    store = SimpleNamespace(close=lambda: None, one=lambda sql: {"snapshot_id": "snapshot"}, manifest=lambda sid: manifest)
    monkeypatch.setattr(evidencekg.config, "initialize", lambda *a, **kw: store)
    def ingest(*args, progress, **kwargs):
        progress(dict(extraction_complete=True, processed_files=7, total_files=7))
    monkeypatch.setattr(ingestion, "ingest_isolated", ingest)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10)
    def prepare(*args, progress, **kwargs):
        assert kwargs["device"] == "auto"
        for completed in range(5):
            progress(indexing_stage="embedding", indexing_completed=completed, indexing_total=4,
                     indexing_device="cuda", indexing_device_name="Test GPU", indexing_updated_at=123)
        progress(indexing_stage="saving_index")
        return {"config": "config"}
    monkeypatch.setattr(module, "prepare", prepare)
    class Ledger:
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def load_snapshot(self, sid): return manifest, {}, []
    monkeypatch.setattr(module, "PostgresWorksetContext", Ledger)
    events = []
    manager._build({"id": "job"}, [], lambda phase, **e: events.append((phase, e)))
    indexed = [e for phase, e in events if "indexing_stage" in e]
    assert [e.get("indexing_completed") for e in indexed] == [0, 4, None]
    assert all(e["processed_files"] == 7 and e["partial_publication"]["snapshot_id"] == "snapshot" for e in indexed)
    assert "indexing_total" not in indexed[-1]
