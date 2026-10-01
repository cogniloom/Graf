"""Local retrieval with LadybugDB graphs and PostgreSQL evidence/workset records."""

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
    return {
        name: version(name) for name in ("numpy", "torch", "transformers", "tokenizers", "psycopg", "ladybug")
    }


def publish_config(target, config):
    target = _safe(target)
    generation = target.parent / "hybrid-configs" / (sha(dump(config)) + ".json")
    with _locked(target.parent, ".hybrid-prepare.lock"):
        if not generation.exists():
            _write(generation, config)
        elif _read(generation) != config:
            raise ValueError("Configuration generation drift")
        atomic(target, dump({"sha256": sha(dump(config)), "value": config}).encode())


def resolve_device(device):
    """Auto uses usable CUDA; explicit CPU/CUDA choices remain authoritative."""
    if device not in ("auto", "cpu", "cuda"):
        raise ValueError("device must be auto, cpu or cuda")
    if device != "auto":
        return device
    import torch

    if torch.cuda.is_available():
        try:
            # Availability alone need not imply that kernels can execute on this GPU.
            torch.ones(1, device="cuda").sum().item()
            return "cuda"
        except RuntimeError:
            pass
    return "cpu"


def prepare(state, models, database_config, *, snapshot=None, device="auto", output=None, progress=None):
    """Explicit verified import; publish config only after the complete index exists."""
    from .graph import prepare_graph
    from .index import DenseIndex
    from .semantic import RERANKER_MODEL_ID, DenseAdapter, snapshot_identity

    device = resolve_device(device)
    device_name = "CPU"
    if device == "cuda":
        import torch

        device_name = torch.cuda.get_device_name(0)

    def report(**counts):
        if progress is not None:
            progress(**counts, indexing_device=device, indexing_device_name=device_name,
                     indexing_updated_at=time.time())

    report(indexing_stage="loading_sources")
    state, models = Path(state).resolve(), Path(models).resolve()
    database_config = Path(database_config).resolve()
    store = ReadOnlyStore(state)
    ledger = PostgresWorkset(database_dsn(database_config))
    try:
        sid = store.snapshot(snapshot)["id"]
        manifest_sha = store.snapshot(sid)["manifest_sha"]
        manifest, segments, links = load_sources(store, sid)
        report(indexing_stage="verifying_sources")
        typed = postings(store, sid)
        cache = {}
        # Keep the single-extraction verification cache effective even though
        # public postings are ordered by occurrence hash. Only reorder this
        # temporary view; the persisted posting order remains unchanged.
        for posting in sorted(typed, key=lambda row: row["extraction_id"]):
            verify_posting(store, posting, cache)
        del cache
        manifest = dict(
            manifest, hybrid_typed_postings=typed, hybrid_manifest_sha=manifest_sha
        )
        report(indexing_stage="importing_snapshot")
        ledger.import_snapshot(sid, manifest_sha, manifest, segments, links)
        graph = prepare_graph(
            state / "hybrid-graphs",
            sid,
            manifest_sha,
            {segment["document_version_id"] for segment in segments.values()},
            links,
        )
        # The verified ledger and graph now own these persisted inputs. Dense
        # indexing needs passages and the binding hash, not validation indexes.
        del typed, manifest, links
        report(indexing_stage="loading_model")
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
        index = DenseIndex(cache_dir, dense, segments, sid, manifest_sha, progress=report if progress is not None else None)
        report(indexing_stage="finalizing")
        cross_identity = snapshot_identity(models / "reranker", RERANKER_MODEL_ID, MODELS["reranker"][1])
        config = {
            "version": 2,
            "graph": graph,
            "snapshot_id": sid,
            "manifest_sha": manifest_sha,
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
        if self.config.get("version") != 2 or "graph" not in self.config:
            raise ValueError(
                "LadybugDB graph not prepared; run prepare-hybrid to build a new serving generation"
            )
        if any(self.config["graph"].get(key) != self.config[key] for key in ("snapshot_id", "manifest_sha")):
            raise ValueError("Graph and serving snapshot identity mismatch")
        if self.config["implementation"] != implementation():
            raise ValueError(
                "Hybrid implementation changed; run prepare-hybrid to bind a fresh configuration"
            )
        if self.config["dependencies"] != dependencies():
            raise ValueError("Hybrid dependencies changed; run prepare-hybrid")
        self.ledger = None
        self.ranker = None
        self.lexical = None
        self.graph = None
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

        from .graph import source_identity
        from .index import DenseIndex
        from .lexical import LexicalIndex
        from .retrieval import HybridDiscovery
        from .semantic import CrossEncoderAdapter, DenseAdapter

        torch.set_num_threads(4)
        c = self.config
        manifest, segments, links = self.ledger.load_snapshot(c["snapshot_id"])
        if manifest["hybrid_manifest_sha"] != c["manifest_sha"]:
            raise ValueError("Serving snapshot identity drift")
        if (
            source_identity(
                c["snapshot_id"],
                c["manifest_sha"],
                {s["document_version_id"] for s in segments.values()},
                links,
            )
            != c["graph"]["source_sha"]
        ):
            raise ValueError("Graph and source-link inventory mismatch")
        self._load_graph()
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
            graph=self.graph,
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
        identity = {
            "config": self.config,
            "question": question,
            "limit": limit,
            "retrieval_code_sha": sha(Path(__file__).with_name("retrieval.py").read_bytes()),
            "query_normalizer_sha": sha(
                (Path(__file__).parent.parent / "knowledge_language.py").read_bytes()
            ),
        }
        if code_context:
            identity["code_context"] = True
        with self._database() as ledger, ledger.query_lock(identity):
            self._load_graph()
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
            result["backend"] = "local-hybrid-ladybugdb"
            result["graph_backend"] = "ladybugdb"
            result["records_backend"] = "postgresql"
            result["generative_model_calls"] = 0
            return result

    def page(self, key, cursor=None, limit=100):
        with self._database() as ledger:
            return ledger.page(key, cursor=cursor, limit=limit)

    def _load_graph(self):
        from .graph import LadybugGraph

        if self.graph is None:
            self.graph = LadybugGraph(self.config["graph"])

    def close(self):
        if self.graph is not None:
            self.graph.close()
            self.graph = None
        if self.lexical:
            self.lexical.close()
        if self.ledger is not None:
            self.ledger.close()
            self.ledger = None
