#!/usr/bin/env python3
"""Pinned LightRAG JSONL worker; see lightrag_adapter.md for setup and limits."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit

VERSION = "1.5.7"


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


class Worker:
    def __init__(self, args):
        self.args = args
        self.rag = None
        self.client = None
        self.failed = False
        self.metadata = {
            "adapter": "lightrag", "package": "lightrag-hku", "version": VERSION,
            "model": args.model, "reasoning_effort": "high", "endpoint": args.endpoint,
            "embedding_model": args.embedding_model, "embedding_dimensions": args.embedding_dims,
            "mode": args.mode, "retries": 0, "final_answer_generated": False,
            "tokenizer_model": "gpt-4o-mini", "chunk_token_size": 1200,
            "chunk_overlap_token_size": 100, "max_parallel_insert": 1,
        }

    async def initialize(self):
        if importlib.metadata.version("lightrag-hku") != VERSION:
            raise RuntimeError(f"requires lightrag-hku=={VERSION}")
        import httpx
        import numpy as np
        from lightrag import LightRAG
        from lightrag.utils import EmbeddingFunc
        self.client = httpx.AsyncClient(
            base_url=self.args.endpoint + "/", trust_env=False, timeout=600,
            headers={"Authorization": "Bearer benchmark-local"},
        )

        async def complete(prompt, system_prompt=None, history_messages=None, **kwargs):
            messages = ([{"role": "system", "content": system_prompt}] if system_prompt else [])
            messages.extend(history_messages or [])
            messages.append({"role": "user", "content": prompt})
            body = {"model": self.args.model, "messages": messages,
                    "reasoning_effort": "high", "stream": False}
            if kwargs.get("keyword_extraction"):
                body["response_format"] = {"type": "json_object"}
            response = await self.client.post("chat/completions", json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("empty/nontext model response")
            return content

        async def embed(texts):
            response = await self.client.post("embeddings", json={
                "model": self.args.embedding_model, "input": texts,
                "dimensions": self.args.embedding_dims, "encoding_format": "float",
            })
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise RuntimeError("embedding indices differ from input batch")
            vectors = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
            if vectors.shape != (len(texts), self.args.embedding_dims) or not np.isfinite(vectors).all():
                raise RuntimeError("invalid embedding dimensions/values")
            return vectors

        self.rag = LightRAG(
            working_dir=str(self.args.workdir / "storage"),
            llm_model_func=complete, llm_model_name=self.args.model,
            llm_model_max_async=1, max_parallel_insert=1,
            chunk_token_size=1200, chunk_overlap_token_size=100,
            embedding_func=EmbeddingFunc(
                embedding_dim=self.args.embedding_dims, max_token_size=8192,
                model_name=self.args.embedding_model, func=embed,
            ),
            embedding_func_max_async=1,
        )
        await self.rag.initialize_storages()
        (self.args.workdir / "adapter-config.json").write_text(json.dumps(self.metadata, indent=2))

    async def operation(self, request):
        if not isinstance(request, dict):
            raise ValueError("operation must be an object")
        if self.failed:
            raise RuntimeError("prior native operation failed; inspect workdir before a fresh run")
        if request.get("op") == "ingest":
            docs = documents(request.get("documents"))
            try:
                track = await self.rag.ainsert(
                    [doc["text"] for doc in docs], ids=[doc["id"] for doc in docs],
                    file_paths=[doc["path"] for doc in docs],
                )
                statuses = await self.rag.doc_status.get_by_ids([doc["id"] for doc in docs])
                # ainsert can swallow provider failures; a tracking ID alone is not success.
                if len(statuses) != len(docs) or any(
                    not row or row.get("status") != "processed" for row in statuses
                ):
                    self.failed = True
                    return {"ok": False, "op": "ingest", "error": "documents not all processed",
                            "native": {"track_id": track, "statuses": statuses}, "config": self.metadata}
                return {"ok": True, "op": "ingest", "documents": len(docs),
                        "native": {"track_id": track, "statuses": statuses}, "config": self.metadata}
            except Exception:
                self.failed = True
                raise
        if request.get("op") == "query":
            from lightrag import QueryParam
            question = request.get("question")
            limit = request.get("limit", 12)
            if not isinstance(question, str) or not question.strip():
                raise ValueError("question must be a nonempty string")
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ValueError("limit must be an integer from 1 to 1000")
            try:
                raw = await self.rag.aquery_data(question, QueryParam(
                    mode=self.args.mode, top_k=limit, chunk_top_k=limit,
                    only_need_context=True, enable_rerank=False,
                ))
            except Exception:
                self.failed = True
                raise
            data = raw.get("data") or {}
            retrieved_sources = [{"path": chunk["file_path"], "text": chunk["content"],
                                  "native_chunk_id": chunk["chunk_id"]}
                                 for chunk in data.get("chunks", [])]
            source_context = "\n\n".join(f"SOURCE: {s['path']}\n{s['text']}" for s in retrieved_sources)
            context = source_context + "\n\nNATIVE GRAPH CONTEXT:\n" + json.dumps(data, ensure_ascii=False)
            ok = raw.get("status") == "success"
            # A completed native empty/failure result is retained, but is not an
            # uncertain provider exception and does not poison later questions.
            return {"ok": ok, "op": "query", "context": context,
                    "sources": retrieved_sources, "native_references": data.get("references", []),
                    "source_context": source_context, "native": raw, "config": self.metadata}
        raise ValueError("unsupported op; expected ingest or query")

    async def close(self):
        if self.rag:
            await self.rag.finalize_storages()
        if self.client:
            await self.client.aclose()


async def run(args, output):
    worker = Worker(args)
    try:
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
            print(json.dumps(result, ensure_ascii=False, default=str), file=output, flush=True)
    finally:
        await worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--endpoint", type=local_endpoint, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--embedding-dims", type=int, default=384)
    parser.add_argument("--mode", choices=["mix", "hybrid", "local", "global", "naive"], default="mix")
    parser.add_argument("--initialize-only", action="store_true")
    args = parser.parse_args()
    if args.embedding_dims < 1:
        parser.error("embedding dimensions must be positive")
    args.workdir = args.workdir.resolve()
    args.workdir.mkdir(parents=True, exist_ok=True)
    # Redirect even native library writes to fd 1, keeping stdout exclusively JSONL.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    os.environ["TIKTOKEN_CACHE_DIR"] = str(args.workdir / "tokenizer-cache")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    try:
        asyncio.run(run(args, output))
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "op": "initialize", "error": str(error),
                          "error_type": type(error).__name__}), file=output, flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
