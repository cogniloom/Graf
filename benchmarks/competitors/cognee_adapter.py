#!/usr/bin/env python3
"""Pinned Cognee JSONL worker; see cognee_adapter.md for setup and limits."""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import importlib.metadata
import json
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

VERSION = "1.6.1"


def local_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            not parsed.port or not parsed.path.rstrip("/").endswith("/v1") or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("endpoint must be http://127.0.0.1:PORT/[optional-system/]v1")
    return value.rstrip("/")


def documents(value: object) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("documents must be a nonempty list")
    seen = set()
    for doc in value:
        if not isinstance(doc, dict) or any(
            not isinstance(doc.get(key), str) or not doc[key].strip()
            for key in ("id", "path", "text")
        ):
            raise ValueError("each document requires nonempty string id, path, text")
        if doc["id"] in seen:
            raise ValueError("duplicate document id")
        seen.add(doc["id"])
    return value


def native_json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, (Path,)):
        return str(value)
    if hasattr(value, "__dict__"):
        return {key: val for key, val in vars(value).items() if not key.startswith("_")}
    return str(value)


def json_safe(value):
    """Retain native UUID-keyed pipeline results as JSON objects, recursively."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return json_safe(native_json(value))


def retrieved_source_nodes(entry):
    """IDs only from the native result, never from a corpus/text search."""
    chunks, documents = {}, {}
    for obj in entry.get("objects_result") or []:
        if not isinstance(obj, dict):
            continue
        for key in ("node1", "node2"):
            node = obj.get(key) or {}
            attributes = node.get("node_attributes") or {}
            identifier = node.get("node_id")
            if identifier and attributes.get("type") == "DocumentChunk":
                chunks[identifier] = None
            elif identifier and attributes.get("type") == "TextDocument":
                documents[identifier] = None
    # CHUNKS exposes native segment identities instead of graph edges.
    for reference in entry.get("evidence") or []:
        if reference.get("kind") == "segment":
            identifier = reference.get("chunk_id") or reference.get("artifact_id")
            if identifier:
                chunks[identifier] = None
    return list(chunks), list(documents)


def project_source(document, manifest, *, chunk=None, dataset_id=None):
    """Project a native document's exact source URI through the input manifest."""
    if not document or document.get("type") != "TextDocument":
        raise ValueError("native TextDocument ancestry missing")
    if chunk is not None and (chunk.get("type") != "DocumentChunk" or
                              chunk.get("document_id") != document.get("id")):
        raise ValueError("native chunk/document ancestry mismatch")
    metadata = document.get("external_metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    uri = metadata.get("_cognee", {}).get("source_uri", "")
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("native document has no local source URI")
    staged_path = unquote(parsed.path)
    source = manifest.get(staged_path)
    if source is None:
        raise ValueError("native source URI is absent from input manifest")
    if chunk is not None:
        text = chunk.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("native chunk text is missing")
    else:
        # Only an actually retrieved TextDocument may hydrate its original input.
        text = Path(staged_path).read_text()
        if hashlib.sha256(text.encode()).hexdigest() != source["sha256"]:
            raise ValueError("staged source content differs from recorded input")
    return {"id": source["id"], "path": source["path"], "text": text,
            "dataset_id": dataset_id, "native_document_id": document["id"],
            "native_chunk_id": chunk["id"] if chunk else None,
            "native_source_uri": uri,
            "lineage": "native document ancestry and exact source URI manifest mapping"}


def configure(args):
    # Before importing Cognee: isolate persisted state and override inherited model routes.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    # Pydantic settings also read .env independently of python-dotenv's disable flag.
    os.chdir(args.workdir)
    for prefix in ("LLM_", "EMBEDDING_", "BAML_", "FALLBACK_", "GRAPH_DATABASE_", "VECTOR_DB_", "DB_"):
        for name in list(os.environ):
            if name.startswith(prefix):
                del os.environ[name]
    settings = {
        "LLM_PROVIDER": "openai", "LLM_MODEL": "openai/" + args.model,
        "LLM_API_KEY": "benchmark-local", "LLM_ENDPOINT": args.endpoint,
        "LLM_ARGS": json.dumps({"reasoning_effort": "high", "num_retries": 0, "max_retries": 0}),
        "STRUCTURED_OUTPUT_FRAMEWORK": "litellm_native",
        "EMBEDDING_PROVIDER": "openai_compatible", "EMBEDDING_MODEL": args.embedding_model,
        "EMBEDDING_DIMENSIONS": str(args.embedding_dims), "EMBEDDING_ENDPOINT": args.endpoint,
        "EMBEDDING_API_KEY": "benchmark-local", "EMBEDDING_MAX_COMPLETION_TOKENS": "8192",
        "DB_PROVIDER": "sqlite", "DB_NAME": "cognee_benchmark",
        "GRAPH_DATABASE_PROVIDER": "ladybug", "VECTOR_DB_PROVIDER": "lancedb",
        "SYSTEM_ROOT_DIRECTORY": str(args.workdir / "system"),
        "DATA_ROOT_DIRECTORY": str(args.workdir / "data"),
        "CACHE_ROOT_DIRECTORY": str(args.workdir / "cache"),
        "COGNEE_LOGS_DIR": str(args.workdir / "logs"),
        "COGNEE_REPOS_DIR": str(args.workdir / "repos"),
        "TIKTOKEN_CACHE_DIR": str(args.workdir / "tokenizer-cache"),
        "COGNEE_TRACING_ENABLED": "false", "TELEMETRY_DISABLED": "true",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True", "MOCK_EMBEDDING": "false",
        "HF_HOME": str(args.workdir / "huggingface-cache"),
        "LLM_RATE_LIMIT_ENABLED": "false", "HUGGINGFACE_TOKENIZER": "",
    }
    os.environ.update(settings)


def disable_retries_and_bind_transport(args):
    """Version-specific transport policy, preserving Cognee prompts and native graph pipeline."""
    import litellm
    from cognee.infrastructure.databases.vector.embeddings.OpenAICompatibleEmbeddingEngine import (
        OpenAICompatibleEmbeddingEngine,
    )
    from cognee.infrastructure.llm.structured_output_framework.litellm_native import native_adapter
    from tenacity import stop_after_attempt
    cls = native_adapter.NativeLiteLLMAdapter
    # Decorators may be wrapped by observability; change the actual retry objects too.
    for method in (cls.acreate_structured_output, OpenAICompatibleEmbeddingEngine.embed_text):
        while method is not None:
            if hasattr(method, "retry"):
                method.retry.stop = stop_after_attempt(1)
            method = getattr(method, "__wrapped__", None)
    cls.MAX_RETRIES = 0
    native_adapter._MAX_VALIDATION_RETRIES = 3

    async def native_prompted_json(self, text_input, system_prompt, response_model, **kwargs):
        # The subscription bridge cannot enforce provider-side response schemas.
        # Its bounded correction loop only retries completed JSON validation failures.
        return await self._acreate_json_fallback(text_input, system_prompt, response_model, **kwargs)
    cls._acreate_structured = native_prompted_json
    original_completion = litellm.acompletion

    async def bound_completion(*positional, **kwargs):
        kwargs.update(model="openai/" + args.model, api_base=args.endpoint,
                      api_key="benchmark-local", reasoning_effort="high",
                      num_retries=0, max_retries=0, timeout=600,
                      allowed_openai_params=["reasoning_effort"])
        kwargs.pop("fallbacks", None)
        return await original_completion(*positional, **kwargs)
    litellm.acompletion = bound_completion
    original_init = OpenAICompatibleEmbeddingEngine.__init__

    def embedding_init(self, *positional, **kwargs):
        original_init(self, *positional, **kwargs)
        self._client.max_retries = 0
    OpenAICompatibleEmbeddingEngine.__init__ = embedding_init


class Worker:
    def __init__(self, args):
        self.args = args
        self.failed = False
        self.cognee = None
        self.metadata = {
            "adapter": "cognee", "package": "cognee", "version": VERSION,
            "model": args.model, "reasoning_effort": "high", "endpoint": args.endpoint,
            "embedding_model": args.embedding_model, "embedding_dimensions": args.embedding_dims,
            "search_type": args.search_type, "only_context": True, "final_answer_generated": False,
            "structured_output": "native prompted JSON, up to 3 completed-output validation attempts",
            "validation_attempts": 3,
            "llm_transport_retries": 0, "embedding_transport_retries": 0,
            "embedding_context_splitting": "native recursive splitting retained",
            "source_projection": "retrieved native chunk/document ancestry to exact source URI manifest key",
            "storage": {"relational": "sqlite", "graph": "ladybug", "vector": "lancedb"},
            "data_per_batch": 1, "chunks_per_batch": 1,
        }

    async def initialize(self):
        if importlib.metadata.version("cognee") != VERSION:
            raise RuntimeError(f"requires cognee=={VERSION}")
        configure(self.args)
        import cognee
        self.cognee = cognee
        disable_retries_and_bind_transport(self.args)
        from cognee.infrastructure.databases.graph import get_graph_engine
        from cognee.infrastructure.databases.relational import create_db_and_tables
        from cognee.infrastructure.databases.vector.get_vector_engine import get_vector_engine_async
        await create_db_and_tables()
        graph = await get_graph_engine()
        await graph.is_empty()
        await get_vector_engine_async()
        (self.args.workdir / "adapter-config.json").write_text(json.dumps(self.metadata, indent=2))

    async def operation(self, request):
        if not isinstance(request, dict):
            raise ValueError("operation must be an object")
        if self.failed:
            raise RuntimeError("prior native operation failed; inspect workdir before a fresh run")
        if request.get("op") == "ingest":
            docs = documents(request.get("documents"))
            source_dir = self.args.workdir / "input"
            source_dir.mkdir(exist_ok=True)
            manifest_path = self.args.workdir / "source-manifest.json"
            manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            paths = []
            for doc in docs:
                name = hashlib.sha256(doc["id"].encode()).hexdigest() + ".txt"
                path = source_dir / name
                if path.exists() and path.read_text() != doc["text"]:
                    raise ValueError("document id already stored with different text; use a new workdir")
                path.write_text(doc["text"])
                manifest[str(path)] = {"id": doc["id"], "path": doc["path"],
                                       "sha256": hashlib.sha256(doc["text"].encode()).hexdigest()}
                paths.append(str(path))
            manifest_path.write_text(json.dumps(manifest, indent=2))
            try:
                added = await self.cognee.add(paths, dataset_name="benchmark", skip_connection_test=True)
                result = await self.cognee.cognify(datasets=["benchmark"], raise_on_error=True,
                                                  data_per_batch=1, chunks_per_batch=1)
            except Exception:
                self.failed = True
                raise
            return {"ok": True, "op": "ingest", "documents": len(docs),
                    "native": {"add": added, "cognify": result}, "config": self.metadata}
        if request.get("op") == "query":
            question = request.get("question")
            limit = request.get("limit", 12)
            if not isinstance(question, str) or not question.strip():
                raise ValueError("question must be a nonempty string")
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ValueError("limit must be an integer from 1 to 1000")
            from cognee.api.v1.search import SearchType
            try:
                result = await self.cognee.search(
                    query_text=question, query_type=SearchType[self.args.search_type],
                    datasets=["benchmark"], top_k=limit, only_context=True,
                    verbose=True, include_references=True,
                )
            except Exception:
                self.failed = True
                raise
            raw = json_safe(result)
            contexts, sources = [], []
            errors = []
            for entry in raw:
                if isinstance(entry, dict):
                    contexts.append(entry.get("context_result"))
                    sources.extend(entry.get("evidence") or [])
                    if entry.get("error"):
                        errors.append(entry["error"])
            if errors:
                self.failed = True
            graph_context = "\n\n".join(
                item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                for item in contexts if item is not None
            )
            hydrated, gaps = await self.hydrate_sources(raw)
            source_context = "\n\n".join(
                f"SOURCE: {source['path']}\n{source['text']}" for source in hydrated
            )
            return {"ok": not errors, "op": "query",
                    "context": source_context + ("\n\nNATIVE GRAPH CONTEXT:\n" + graph_context if graph_context else ""),
                    "source_context": source_context, "graph_context": graph_context,
                    "sources": hydrated, "native_sources": sources, "lineage_gaps": gaps,
                    "native": raw, "errors": errors, "config": self.metadata}
        raise ValueError("unsupported op; expected ingest or query")


    async def hydrate_sources(self, raw):
        """Read only retrieved source nodes and their exact native parent documents."""
        from cognee.context_global_variables import set_database_global_context_variables
        from cognee.infrastructure.databases.graph import get_graph_engine
        from cognee.modules.data.methods.get_dataset import get_dataset
        from cognee.modules.users.methods.get_default_user import get_default_user
        manifest_path = self.args.workdir / "source-manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        user = await get_default_user()
        sources, gaps = [], []
        for entry in raw:
            chunks, documents = retrieved_source_nodes(entry)
            if not chunks and not documents:
                continue
            dataset_id = entry.get("dataset_id")
            if not dataset_id:
                gaps.append({"error": "native dataset ID missing", "chunk_ids": chunks})
                continue
            dataset = await get_dataset(user.id, UUID(dataset_id))
            if dataset is None:
                raise RuntimeError("retrieved dataset is not owned by the local benchmark user")
            async with set_database_global_context_variables(dataset.id, dataset.owner_id):
                graph = await get_graph_engine()
                covered_documents = set()
                for chunk_id in chunks:
                    chunk = await graph.get_node(chunk_id)
                    document_id = (chunk or {}).get("document_id")
                    document = await graph.get_node(document_id) if document_id else None
                    try:
                        sources.append(project_source(document, manifest, chunk=chunk, dataset_id=dataset_id))
                        covered_documents.add(document_id)
                    except ValueError as error:
                        gaps.append({"chunk_id": chunk_id, "error": str(error)})
                for document_id in documents:
                    if document_id in covered_documents:
                        continue
                    document = await graph.get_node(document_id)
                    try:
                        sources.append(project_source(document, manifest, dataset_id=dataset_id))
                    except ValueError as error:
                        gaps.append({"document_id": document_id, "error": str(error)})
        return sources, gaps


async def run(args, output):
    worker = Worker(args)
    await worker.initialize()
    if args.initialize_only:
        print(json.dumps({"ok": True, "op": "initialize", "config": worker.metadata}), file=output, flush=True)
        return
    for line in sys.stdin:
        try:
            result = await worker.operation(json.loads(line))
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            result = {"ok": False, "error": str(error), "error_type": type(error).__name__,
                      "config": worker.metadata}
        print(json.dumps(json_safe(result), ensure_ascii=False), file=output, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--endpoint", type=local_endpoint, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--embedding-dims", type=int, default=384)
    parser.add_argument("--search-type", choices=["GRAPH_COMPLETION", "CHUNKS"], default="GRAPH_COMPLETION")
    parser.add_argument("--initialize-only", action="store_true")
    args = parser.parse_args()
    if args.embedding_dims < 1:
        parser.error("embedding dimensions must be positive")
    args.workdir = args.workdir.resolve()
    args.workdir.mkdir(parents=True, exist_ok=True)
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        asyncio.run(run(args, output))
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "op": "initialize", "error": str(error),
                          "error_type": type(error).__name__}), file=output, flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
