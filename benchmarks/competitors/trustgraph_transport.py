"""TrustGraph model transport bindings for the local benchmark gateway.

Native TrustGraph ingestion, extraction, storage, graph traversal and prompts remain
in the official image; these two processors only bind inference to the shared gateway.
"""
import os

import httpx
from openai import AsyncOpenAI, OpenAI
from trustgraph.base import EmbeddingsService
from trustgraph.model.text_completion.openai import Processor as NativeCompletion


def endpoint():
    value = os.environ["BENCHMARK_ENDPOINT"]
    from urllib.parse import urlsplit
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port
            or url.path.rstrip("/") not in ("/v1", "/trustgraph/v1")
            or url.username or url.password or url.query or url.fragment):
        raise ValueError("Benchmark transport only accepts loopback gateway")
    return value


class Completion(NativeCompletion):
    def __init__(self, **params):
        super().__init__(**(params | {"model": "gpt-6-luna", "thinking": "high",
                                      "variant": "openai", "url": endpoint(),
                                      "api_key": "benchmark-local"}))
        self.openai = OpenAI(base_url=endpoint(), api_key="benchmark-local",
                             max_retries=0, timeout=1800,
                             http_client=httpx.Client(transport=httpx.HTTPTransport(
                                 uds='/benchmark-socket/gateway.sock'), timeout=1800))
        self.failed = False

    async def generate_content(self, system, prompt, model=None, temperature=None, **kwargs):
        if self.failed:
            raise RuntimeError("Prior model failure: benchmark transport halted, no retry")
        try:
            return await super().generate_content(system, prompt, "gpt-6-luna", temperature, **kwargs)
        except Exception as exc:
            self.failed = True
            # Normalize retryable native errors to a permanent error so the
            # message consumer does not requeue a possibly completed model call.
            raise RuntimeError(f"Benchmark inference failed; no retry: {exc}") from exc

    def supports_streaming(self):
        return False


class Embeddings(EmbeddingsService):
    def __init__(self, **params):
        self.model = os.getenv("BENCHMARK_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        self.dimensions = int(os.getenv("BENCHMARK_EMBEDDING_DIMS", "384"))
        super().__init__(**(params | {"model": self.model}))
        self.client = AsyncOpenAI(base_url=endpoint(), api_key="benchmark-local",
                                  max_retries=0, timeout=600,
                                  http_client=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(
                                      uds='/benchmark-socket/gateway.sock'), timeout=600))

    async def on_embeddings(self, texts, model=None):
        result = await self.client.embeddings.create(model=self.model, input=texts,
                                                      encoding_format="float")
        vectors = [e.embedding for e in sorted(result.data, key=lambda e: e.index)]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise ValueError("Unexpected embedding response count or dimensions")
        return vectors
