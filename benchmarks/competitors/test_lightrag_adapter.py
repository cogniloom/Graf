"""Contract regressions; native live smoke evidence is documented separately."""
import argparse
import asyncio
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("lightrag_worker", Path(__file__).with_name("lightrag_adapter.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class LightRAGContractTests(unittest.TestCase):
    def test_paid_or_credentialed_endpoint_rejected(self):
        for endpoint in ("https://api.openai.com/v1", "http://localhost:8888/v1",
                         "http://benchmark-local@127.0.0.1:8888/v1", "http://127.0.0.1:8888/v1?key=x"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                adapter.local_endpoint(endpoint)
        self.assertEqual(adapter.local_endpoint("http://127.0.0.1:18791/lightrag/v1/"),
                         "http://127.0.0.1:18791/lightrag/v1")

    def test_ambiguous_document_identity_rejected(self):
        doc = {"id": "a", "path": "a.txt", "text": "source"}
        with self.assertRaises(ValueError):
            adapter.documents([doc, doc])
        with self.assertRaises(ValueError):
            adapter.documents([{**doc, "text": " "}])

    def test_swallowed_native_ingest_failure_is_not_success(self):
        class DocStatus:
            async def get_by_ids(self, ids):
                return [{"status": "failed", "error_msg": "transport failed"}]

        class Native:
            doc_status = DocStatus()

            async def ainsert(self, *args, **kwargs):
                return "track-does-not-prove-success"

        worker = adapter.Worker(argparse.Namespace(model="gpt-6-luna", endpoint="http://127.0.0.1:1/v1",
                               embedding_model="local", embedding_dims=384, mode="mix"))
        worker.rag = Native()
        request = {"op": "ingest", "documents": [{"id": "a", "path": "a.txt", "text": "source"}]}
        result = asyncio.run(worker.operation(request))
        self.assertFalse(result["ok"])
        self.assertEqual(result["native"]["statuses"][0]["error_msg"], "transport failed")
        with self.assertRaisesRegex(RuntimeError, "prior native operation failed"):
            asyncio.run(worker.operation(request))


if __name__ == "__main__":
    unittest.main()
