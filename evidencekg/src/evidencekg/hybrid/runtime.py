"""Default local retrieval service. PostgreSQL persistence; no generative clients."""

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from evidencekg.db import atomic, dump, sha
from evidencekg.experiments import _locked, _read, _safe, _write
from evidencekg.relationships import postings, verify_posting

from .download_models import MODELS
from .postgres import PostgresWorkset
from .sources import ReadOnlyStore, load_sources


def database_dsn(path):
    from psycopg.conninfo import make_conninfo

    value = json.loads(Path(path).read_text())
    allowed = {"host", "port", "dbname", "user", "password_file"}
    if set(value) != allowed:
        raise ValueError("Database config requires host, port, dbname, user, password_file")
    value["password"] = Path(value.pop("password_file")).read_text().strip()
    return make_conninfo(**value, connect_timeout=5, application_name="evidencekg-hybrid")


def implementation():
    return {p.name: sha(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))}


def dependencies():
    return {name: version(name) for name in ("numpy", "torch", "transformers", "tokenizers", "psycopg")}


def publish_config(target, config):
    target = _safe(target)
    generation = target.parent / "hybrid-configs" / (sha(dump(config)) + ".json")
    with _locked(target.parent, ".hybrid-prepare.lock"):
        if not generation.exists():
            _write(generation, config)
        elif _read(generation) != config:
            raise ValueError("Configuration generation drift")
        atomic(target, dump({"sha256": sha(dump(config)), "value": config}).encode())


def prepare(state, models, database_config, *, snapshot=None, device="cuda", output=None):
    """Explicit verified import; publish config only after the complete index exists."""
    from .index import DenseIndex
    from .semantic import RERANKER_MODEL_ID, DenseAdapter, snapshot_identity

    state, models = Path(state).resolve(), Path(models).resolve()
    database_config = Path(database_config).resolve()
    store = ReadOnlyStore(state)
    ledger = PostgresWorkset(database_dsn(database_config))
    try:
        sid = store.snapshot(snapshot)["id"]
        manifest, segments, links = load_sources(store, sid)
        typed = postings(store, sid)
        cache = {}
        for posting in typed:
            verify_posting(store, posting, cache)
        manifest = dict(
            manifest, hybrid_typed_postings=typed, hybrid_manifest_sha=store.snapshot(sid)["manifest_sha"]
        )
        ledger.import_snapshot(sid, manifest["hybrid_manifest_sha"], manifest, segments, links)
        dense = DenseAdapter.from_local(
            models / "dense", MODELS["dense"][1], max_tokens=1024, batch_size=4, device=device
        )
        index_generation = sha(
            dump(
                {
                    "model": asdict(dense.identity),
                    "device": device,
                    "semantic": implementation()["semantic.py"],
                    "index": implementation()["index.py"],
                    "dependencies": dependencies(),
                    "max_tokens": 1024,
                    "batch_size": 4,
                }
            )
        )
        cache_dir = state / "hybrid" / sid / index_generation
        index = DenseIndex(cache_dir, dense, segments, sid, manifest["hybrid_manifest_sha"])
        cross_identity = snapshot_identity(models / "reranker", RERANKER_MODEL_ID, MODELS["reranker"][1])
        config = {
            "version": 1,
            "snapshot_id": sid,
            "manifest_sha": manifest["hybrid_manifest_sha"],
            "models": str(models),
            "database_config": str(database_config),
            "dense_index": str(cache_dir),
            "device": device,
            "dense_identity": asdict(dense.identity),
            "reranker_identity": asdict(cross_identity),
            "vectors_sha": index.manifest["vectors_sha"],
            "implementation": implementation(),
            "dependencies": dependencies(),
        }
        target = Path(output) if output else state / "hybrid.json"
        publish_config(target, config)
        return {
            "config": str(target),
            "snapshot_id": sid,
            "segments": len(segments),
            "index_seconds": index.manifest["index_seconds"],
            "generative_calls": 0,
        }
    finally:
        ledger.close()
        store.close()


class Runtime:
    def __init__(self, state, config=None):
        path = Path(config or os.environ.get("EVIDENCEKG_HYBRID_CONFIG", Path(state) / "hybrid.json"))
        if not path.is_file():
            raise ValueError(
                "Hybrid retrieval is the default; run prepare-hybrid first (see HYBRID.md). No mechanical fallback."
            )
        self.config = _read(path)
        if self.config["implementation"] != implementation():
            raise ValueError(
                "Hybrid implementation changed; run prepare-hybrid to bind a fresh configuration"
            )
        if self.config["dependencies"] != dependencies():
            raise ValueError("Hybrid dependencies changed; run prepare-hybrid")
        self.ledger = None
        self.ranker = None
        self.lexical = None
        self.lock = threading.RLock()
        self.verified = False

    @contextmanager
    def _database(self):
        import psycopg

        with self.lock:
            try:
                if self.ledger is None:
                    self.ledger = PostgresWorkset(database_dsn(self.config["database_config"]))
                    if self.ranker is not None:
                        self.ranker.worksets = self.ledger
                yield self.ledger
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                if self.ledger is not None:
                    self.ledger.close()
                    self.ledger = None
                raise ValueError(
                    "PostgreSQL unavailable; this request failed. The next request will reconnect; no fallback or automatic replay."
                ) from exc

    def _verify_models(self):
        if self.verified:
            return
        from .semantic import snapshot_identity

        for name, expected in [
            ("dense", self.config["dense_identity"]),
            ("reranker", self.config["reranker_identity"]),
        ]:
            actual = snapshot_identity(
                Path(self.config["models"]) / name, expected["model_id"], expected["revision"]
            )
            if asdict(actual) != expected:
                raise ValueError("Local model identity drift")
        self.verified = True

    def _load(self):
        if self.ranker is not None:
            return
        import torch

        from .index import DenseIndex
        from .lexical import LexicalIndex
        from .retrieval import HybridDiscovery
        from .semantic import CrossEncoderAdapter, DenseAdapter

        torch.set_num_threads(4)
        c = self.config
        manifest, segments, links = self.ledger.load_snapshot(c["snapshot_id"])
        if manifest["hybrid_manifest_sha"] != c["manifest_sha"]:
            raise ValueError("Serving snapshot identity drift")
        dense = DenseAdapter.from_local(
            Path(c["models"]) / "dense", MODELS["dense"][1], max_tokens=1024, batch_size=4, device=c["device"]
        )
        if asdict(dense.identity) != c["dense_identity"]:
            raise ValueError("Loaded dense model identity drift")
        index = DenseIndex(c["dense_index"], dense, segments, c["snapshot_id"], c["manifest_sha"])
        if index.manifest["vectors_sha"] != c["vectors_sha"]:
            raise ValueError("Dense index drift")
        cross = CrossEncoderAdapter.from_local(
            Path(c["models"]) / "reranker",
            MODELS["reranker"][1],
            max_tokens=1024,
            batch_size=4,
            device=c["device"],
        )
        if asdict(cross.identity) != c["reranker_identity"]:
            raise ValueError("Loaded reranker identity drift")
        self.lexical = LexicalIndex(segments)
        self.ranker = HybridDiscovery(
            None,
            c["snapshot_id"],
            index.search,
            cross,
            self.ledger,
            sources=(manifest, segments, links),
            typed=manifest["hybrid_typed_postings"],
            lexical=self.lexical,
            model_identity={
                "serving_config_sha": sha(dump(c)),
                "dense": c["dense_identity"],
                "reranker": c["reranker_identity"],
                "dense_index_sha": c["vectors_sha"],
                "device": c["device"],
                "precision": "float16" if c["device"].startswith("cuda") else "float32",
                "max_tokens": 1024,
                "batch_size": 4,
            },
        )

    def retrieve(self, question, limit=12, snapshot=None, *, code_context=False):
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError("Expected nonempty question, at most4096 characters")
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("Expected limit1..12")
        if type(code_context) is not bool:
            raise ValueError("Expected boolean code_context")
        if snapshot is not None and snapshot != self.config["snapshot_id"]:
            raise ValueError("Snapshot not prepared for hybrid retrieval")
        begin = time.monotonic()
        identity = {"config": self.config, "question": question, "limit": limit}
        if code_context:
            identity["code_context"] = True
        with self._database() as ledger, ledger.query_lock(identity):
            self._verify_models()
            result = self.ledger.get_result(identity)
            cached = result is not None
            if not cached:
                self._load()
                result = self.ranker.retrieve(question, limit=limit)
                if code_context:
                    from .code_relationships import build_relationship_context

                    result["code_context"] = build_relationship_context(
                        self.ranker.segments, question, result, reranker=self.ranker.reranker
                    )
                self.ledger.put_result(identity, result)
            else:
                # Validate the retained inventory before advertising continuation.
                self.ledger.page(result["workset_id"], limit=1)
            result["cache"] = {"hit": cached, "request_seconds": time.monotonic() - begin}
            result["backend"] = "local-hybrid-postgresql"
            result["generative_model_calls"] = 0
            return result

    def page(self, key, cursor=None, limit=100):
        with self._database() as ledger:
            return ledger.page(key, cursor=cursor, limit=limit)

    def close(self):
        if self.lexical:
            self.lexical.close()
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None
