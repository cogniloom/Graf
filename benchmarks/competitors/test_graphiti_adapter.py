"""Transport policy and uncertain-operation persistence, without model calls."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from benchmarks.competitors.graphiti_adapter import CompletionPolicy, Worker, local_endpoint


class AdapterTests(unittest.TestCase):
    def test_no_external_provider_endpoints(self):
        for endpoint in ("https://api.openai.com/v1", "http://localhost:12/v1",
                         "http://user:key@127.0.0.1:12/v1", "http://127.0.0.1:12/v1?key=x"):
            with self.assertRaises(ValueError):
                local_endpoint(endpoint)
        self.assertEqual(local_endpoint("http://127.0.0.1:18791/graphiti/v1"),
                         "http://127.0.0.1:18791/graphiti/v1")

    def test_model_policy_overrides_native_defaults(self):
        calls = []
        class Completion:
            async def create(self, **kwargs):
                calls.append(kwargs)
                return "response"
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=Completion()))
        response = asyncio.run(CompletionPolicy(sdk).create(model="other", messages=[]))
        self.assertEqual(response, "response")
        self.assertEqual(calls[0]["model"], "gpt-6-luna")
        self.assertEqual(calls[0]["reasoning_effort"], "high")

    def test_uncertain_query_blocks_restart_without_retry(self):
        class Graph:
            calls = 0
            async def search(self, *args, **kwargs):
                self.calls += 1
                raise TimeoutError("unknown outcome")
        with tempfile.TemporaryDirectory() as root:
            args = SimpleNamespace(workdir=root)
            graph = Graph()
            worker = Worker(args, graph)
            with self.assertRaises(TimeoutError):
                asyncio.run(worker.operate({"op": "query", "question": "Who?"}))
            restarted = Worker(args, graph)
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                asyncio.run(restarted.operate({"op": "query", "question": "Who?"}))
            self.assertEqual(graph.calls, 1)
            self.assertTrue((Path(root) / "graphiti-state.json").exists())


if __name__ == "__main__":
    unittest.main()
