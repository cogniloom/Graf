"""Cognee worker input and isolation contracts; does not claim live retrieval proof."""
import argparse
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

spec = importlib.util.spec_from_file_location("cognee_worker", Path(__file__).with_name("cognee_adapter.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class CogneeContractTests(unittest.TestCase):
    def test_source_projection_requires_exact_native_document_ancestry(self):
        document = {"id": "native-doc", "type": "TextDocument", "external_metadata":
                    json.dumps({"_cognee": {"source_uri": "file:///staged/a.txt"}})}
        chunk = {"id": "native-chunk", "type": "DocumentChunk", "document_id": "native-doc", "text": "original"}
        manifest = {"/staged/a.txt": {"id": "input-a", "path": "reports/original.txt", "sha256": "unused"}}
        source = adapter.project_source(document, manifest, chunk=chunk, dataset_id="dataset")
        self.assertEqual(source["path"], "reports/original.txt")
        self.assertEqual(source["native_chunk_id"], "native-chunk")
        self.assertEqual(source["text"], "original")
        with self.assertRaisesRegex(ValueError, "ancestry mismatch"):
            adapter.project_source(document, manifest, chunk={**chunk, "document_id": "unrelated"})
        with self.assertRaisesRegex(ValueError, "absent from input manifest"):
            adapter.project_source(document, {"/other/a.txt": manifest["/staged/a.txt"]}, chunk=chunk)

    def test_hydration_selects_only_native_retrieved_source_nodes(self):
        entry = {"objects_result": [{
            "node1": {"node_id": "chunk", "node_attributes": {"type": "DocumentChunk"}},
            "node2": {"node_id": "entity", "node_attributes": {"type": "Entity"}},
        }], "evidence": [{"kind": "graph_node", "artifact_id": "unrelated"}]}
        self.assertEqual(adapter.retrieved_source_nodes(entry), (["chunk"], []))

    def test_native_pipeline_uuid_keys_survive_jsonl_encoding(self):
        identifier = UUID("00000000-0000-0000-0000-000000000001")
        raw = {identifier: {"dataset_id": identifier, "status": "completed"}}
        encoded = json.loads(json.dumps(adapter.json_safe(raw)))
        self.assertEqual(encoded[str(identifier)]["dataset_id"], str(identifier))
        self.assertEqual(encoded[str(identifier)]["status"], "completed")

    def test_only_loopback_endpoint_accepted(self):
        for endpoint in ("https://api.openai.com/v1", "http://example.com:1234/v1",
                         "http://127.0.0.1:1234/v1?api_key=x", "http://key@127.0.0.1:1234/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                adapter.local_endpoint(endpoint)
        self.assertEqual(adapter.local_endpoint("http://127.0.0.1:18791/cognee/v1"),
                         "http://127.0.0.1:18791/cognee/v1")

    def test_inherited_paid_stage_configuration_is_removed(self):
        cwd = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                "LLM_EXTRACTION_MODEL": "other-model", "LLM_QUERY_ENDPOINT": "https://api.openai.com/v1",
                "FALLBACK_API_KEY": "do-not-use", "EMBEDDING_API_KEY": "do-not-use",
                "MOCK_EMBEDDING": "true",
            }):
                args = argparse.Namespace(workdir=Path(directory), model="gpt-6-luna",
                    endpoint="http://127.0.0.1:18791/cognee/v1", embedding_model="local", embedding_dims=384)
                adapter.configure(args)
                self.assertNotIn("LLM_EXTRACTION_MODEL", os.environ)
                self.assertNotIn("LLM_QUERY_ENDPOINT", os.environ)
                self.assertNotIn("FALLBACK_API_KEY", os.environ)
                self.assertEqual(os.environ["LLM_API_KEY"], "benchmark-local")
                self.assertEqual(os.environ["EMBEDDING_API_KEY"], "benchmark-local")
                self.assertEqual(os.environ["MOCK_EMBEDDING"], "false")
                self.assertEqual(json.loads(os.environ["LLM_ARGS"])["reasoning_effort"], "high")
                os.chdir(cwd)
        finally:
            os.chdir(cwd)

    def test_invalid_or_duplicate_document_identity_rejected(self):
        doc = {"id": "a", "path": "a.txt", "text": "source"}
        with self.assertRaises(ValueError):
            adapter.documents([doc, doc])
        with self.assertRaises(ValueError):
            adapter.documents([{**doc, "path": None}])


if __name__ == "__main__":
    unittest.main()
