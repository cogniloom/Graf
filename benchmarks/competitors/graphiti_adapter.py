#!/usr/bin/env python3
"""JSON-line Graphiti 0.30.2 native episode-ingestion / hybrid-edge retrieval."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from contextlib import redirect_stdout
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

MODEL = "gpt-6-luna"


def local_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or not parsed.port or parsed.path.rstrip("/") not in ("/v1", "/graphiti/v1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("endpoint must be http://127.0.0.1:PORT/v1")
    return value.rstrip("/")


class CompletionPolicy:
    """Apply benchmark generation policy without changing native Graphiti prompts."""
    def __init__(self, sdk):
        self.sdk = sdk
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        kwargs["model"] = MODEL
        kwargs["reasoning_effort"] = "high"
        return await self.sdk.chat.completions.create(**kwargs)


def make_graph(args):
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from openai import AsyncOpenAI

    class NoRetryClient(OpenAIGenericClient):
        async def _generate_response_with_retry(self, *positional, **keywords):
            return await self._generate_response(*positional, **keywords)

    sdk = AsyncOpenAI(base_url=local_endpoint(args.endpoint), api_key="benchmark-local",
                      max_retries=0, timeout=1800)
    policy = CompletionPolicy(sdk)
    config = LLMConfig(api_key="benchmark-local", base_url=args.endpoint,
                       model=MODEL, small_model=MODEL)
    graph = Graphiti(
        args.neo4j_uri, "neo4j", "benchmark-local",
        llm_client=NoRetryClient(config=config, client=policy, structured_output_mode="json_object"),
        embedder=OpenAIEmbedder(config=OpenAIEmbedderConfig(
            embedding_model=args.embedding_model, embedding_dim=args.embedding_dims), client=sdk),
        cross_encoder=OpenAIRerankerClient(config=config, client=policy),
        max_coroutines=1,
    )
    return graph, sdk


class Worker:
    def __init__(self, args, graph):
        self.args, self.graph = args, graph
        self.root = Path(args.workdir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / "graphiti-state.json"
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
        else:
            self.state = {"group_id": "benchmark_" + uuid.uuid4().hex,
                          "documents": {}, "pending": None}
            self.save()

    def save(self):
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2) + "\n")
        temporary.replace(self.state_file)

    async def operate(self, request):
        if self.state["pending"]:
            raise RuntimeError("Prior operation failed or is uncertain; use a fresh workdir, do not retry")
        op = request["op"]
        if op == "initialize":
            await self.graph.build_indices_and_constraints()
            return {"ok": True, "op": op, "version": version("graphiti-core"),
                    "group_id": self.state["group_id"], "model": MODEL,
                    "reasoning_effort": "high", "embedding_model": self.args.embedding_model,
                    "embedding_dims": self.args.embedding_dims, "model_calls": 0,
                    "structured_output_mode": "native json_object with schema in prompt"}
        if op == "ingest":
            documents = request["documents"]
            if not isinstance(documents, list):
                raise ValueError("documents must be an array")
            ids = [d["id"] for d in documents]
            if len(ids) != len(set(ids)) or any(i in self.state["documents"] for i in ids):
                raise ValueError("document IDs must be unique; ingestion is never retried")
            for d in documents:
                if not all(isinstance(d.get(k), str) for k in ("id", "path", "text")):
                    raise ValueError("documents require string id, path and text")
            from graphiti_core.nodes import EpisodeType
            self.state["pending"] = {"op": op, "ids": ids}
            self.save()
            await self.graph.build_indices_and_constraints()
            for doc in documents:
                result = await self.graph.add_episode(
                    name=doc["id"], episode_body=doc["text"], source=EpisodeType.text,
                    source_description=doc["path"], group_id=self.state["group_id"],
                    reference_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    update_communities=False,
                )
                self.state["documents"][doc["id"]] = {
                    "path": doc["path"], "episode_uuid": result.episode.uuid,
                    "sha256": hashlib.sha256(doc["text"].encode()).hexdigest(),
                }
                self.save()
            self.state["pending"] = None
            self.save()
            return {"ok": True, "op": op, "documents_ingested": len(documents),
                    "native_episodes": self.state["documents"]}
        if op == "query":
            question, limit = request["question"], request.get("limit", 12)
            if not isinstance(question, str) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError("question must be text and limit must be 1..100")
            self.state["pending"] = {"op": op, "question": question}
            self.save()
            edges = await self.graph.search(question, group_ids=[self.state["group_id"]], num_results=limit)
            from graphiti_core.nodes import EpisodicNode
            episode_ids = sorted({uid for edge in edges for uid in edge.episodes})
            episodes = await EpisodicNode.get_by_uuids(self.graph.driver, episode_ids) if episode_ids else []
            known = {e.uuid: e for e in episodes}
            contexts = []
            for edge in edges:
                sources = [{"episode_uuid": uid, "id": known[uid].name,
                            "path": known[uid].source_description}
                           for uid in edge.episodes if uid in known]
                contexts.append({"text": edge.fact, "native_id": edge.uuid,
                                 "sources": sources, "episode_uuids": edge.episodes})
            self.state["pending"] = None
            self.save()
            hydrated = [{"episode_uuid": e.uuid, "id": e.name,
                         "path": e.source_description, "text": e.content}
                        for e in episodes]
            fact_context = "\n\n".join(c["text"] for c in contexts)
            source_context = "\n\n".join(
                f"SOURCE: {e['path']}\nDOCUMENT_ID: {e['id']}\n{e['text']}" for e in hydrated)
            return {"ok": True, "op": op, "contexts": contexts,
                    "context": f"EXTRACTED GRAPH FACTS:\n{fact_context}\n\nNATIVE-LINKED ORIGINAL SOURCES:\n{source_context}",
                    "hydrated_sources": hydrated,
                    "sources": hydrated,
                    "retrieved_fact_count": len(contexts),
                    "retrieved_fact_bytes": sum(len(c["text"].encode()) for c in contexts),
                    "hydrated_source_count": len(hydrated),
                    "hydrated_source_bytes": sum(len(e["text"].encode()) for e in hydrated),
                    "native_edges": [e.model_dump(mode="json") for e in edges],
                    "retrieval": "Graphiti.search EDGE_HYBRID_SEARCH_RRF",
                    "citation_limit": "Facts are model-extracted; episode associations are native, not exact-span citations"}
        raise ValueError(f"Unsupported op: {op}")


async def run(args):
    protocol = sys.stdout
    with redirect_stdout(sys.stderr):
        graph, sdk = make_graph(args)
        worker = Worker(args, graph)
    try:
        for line in sys.stdin:
            try:
                with redirect_stdout(sys.stderr):
                    result = await worker.operate(json.loads(line))
            except Exception as exc:
                logging.exception("Graphiti operation failed")
                result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
            print(json.dumps(result), file=protocol, flush=True)
    finally:
        with redirect_stdout(sys.stderr):
            await graph.close()
            await sdk.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--endpoint", required=True, type=local_endpoint)
    parser.add_argument("--neo4j-uri", default="bolt://127.0.0.1:17687")
    parser.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--embedding-dims", type=int, default=384)
    args = parser.parse_args()
    if args.embedding_dims <= 0:
        parser.error("embedding dimensions must be positive")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
