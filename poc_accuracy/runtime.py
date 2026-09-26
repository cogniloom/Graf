"""Explicit local-model setup. Importing performs no inference or provider calls."""

import time
from dataclasses import asdict
from pathlib import Path

from evidencekg.db import atomic, dump
from evidencekg.experiments import _read

from .download_models import MODELS
from .index import DenseIndex
from .retrieval import HybridDiscovery
from .semantic import CrossEncoderAdapter, DenseAdapter
from .sources import ReadOnlyStore, load_sources
from .workset import Workset

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".evidencekg-private/poc-accuracy-20260926"
OLD = PROJECT / ".evidencekg-private/benchmark-1000-20260925"


def setup(*, rerank=True):
    import torch

    torch.set_num_threads(4)
    begin = time.monotonic()
    corpus = (
        _read(ROOT / "protocol.json")
        if (ROOT / "protocol.json").exists()
        else _read(OLD / "experiment/experiment.json")
    )["corpus"]
    snapshot = corpus["snapshot_id"]
    store = ReadOnlyStore(OLD / "model-vault")
    if store.snapshot(snapshot)["manifest_sha"] != corpus["manifest_sha"]:
        raise ValueError("Source manifest drift")
    _, segments, _ = load_sources(store, snapshot)
    dense = DenseAdapter.from_local(
        ROOT / "models/dense", MODELS["dense"][1], max_tokens=1024, batch_size=4, device="cuda"
    )
    index = DenseIndex(ROOT / "dense-index-v2", dense, segments, snapshot, corpus["manifest_sha"])
    if not rerank:
        store.close()
        return index
    cross = CrossEncoderAdapter.from_local(
        ROOT / "models/reranker", MODELS["reranker"][1], max_tokens=1024, batch_size=4, device="cuda"
    )
    ledger = Workset(ROOT / "worksets.sqlite")
    ranker = HybridDiscovery(
        store,
        snapshot,
        index.search,
        cross,
        ledger,
        model_identity={
            "dense": asdict(dense.identity),
            "reranker": asdict(cross.identity),
            "dense_index_sha": index.manifest["vectors_sha"],
            "max_tokens": 1024,
            "batch_size": 4,
            "device": "cuda",
            "precision": "float16",
        },
    )
    return (
        store,
        ledger,
        ranker,
        {"setup_seconds": time.monotonic() - begin, "index_seconds": index.manifest["index_seconds"]},
    )


if __name__ == "__main__":
    index = setup(rerank=False)
    atomic(
        ROOT / "index-status.json",
        dump(
            {
                "segments": index.manifest["segments"],
                "windows": len(index.sources),
                "index_seconds": index.manifest["index_seconds"],
                "vectors_sha": index.manifest["vectors_sha"],
            }
        ).encode(),
    )
    print((ROOT / "index-status.json").read_text(), flush=True)
    store, ledger, ranker, timing = setup()
    atomic(ROOT / "ranker-identity.json", dump(ranker.identity).encode())
    store.close()
    ledger.close()
