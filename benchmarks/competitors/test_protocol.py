"""Regression checks for accounting and fail-closed benchmark boundaries."""
import io
import json
import os
import selectors
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from benchmarks.competitors.gateway import Gateway, subscription_env
from benchmarks.competitors.protocol import Adapter, usage_delta
from benchmarks.competitors.report import citation_catalog, citation_gate, evidence_coverage, percentile


class AccountingTests(unittest.TestCase):
    def test_usage_counts_cached_tokens_once(self):
        row = {"usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 12}}
        result = usage_delta({}, {"a": row})
        self.assertEqual(result["input_tokens"] + result["output_tokens"], 112)
        self.assertEqual(result["cached_input_tokens"], 80)
        self.assertEqual(result["calls"], 1)

    def test_missing_receipts_fail(self):
        with self.assertRaises(ValueError):
            usage_delta({"lost": {}}, {})

    def test_no_paid_environment_fallback(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "must-not-forward",
                                      "OPENAI_BASE_URL": "http://billing", "SAFE": "kept"}, clear=True):
            self.assertEqual(subscription_env(), {"SAFE": "kept"})

    def test_model_substitution_rejected_before_call(self):
        gateway = Gateway.__new__(Gateway)
        with self.assertRaises(ValueError):
            gateway.chat({"model": "other"}, "test")

    def test_wrong_effort_rejected_before_call(self):
        gateway = Gateway.__new__(Gateway)
        with self.assertRaises(ValueError):
            gateway.chat({"model": "gpt-6-luna", "reasoning_effort": "low"}, "test")

    def test_halted_gateway_never_retries(self):
        import threading
        gateway = Gateway.__new__(Gateway)
        gateway.lock = threading.Lock()
        gateway.failure = "prior-call"
        with self.assertRaisesRegex(RuntimeError, "halted"):
            gateway.chat({"model": "gpt-6-luna", "messages": [{"role": "user", "content": "x"}]}, "test")

    def test_quote_must_be_in_retrieved_context_and_source(self):
        answer = {"citations": [{"path": "a.txt", "quote": "Approved 12 units."}]}
        retrieved = [{"path": "a.txt", "text": "Approved 12 units."}]
        self.assertFalse(citation_gate(answer, {"a.txt": "Approved 12 units."}, "Requested 15 units.", retrieved)["citations_valid"])
        self.assertTrue(citation_gate(answer, {"a.txt": "Approved 12 units."}, "SOURCE: a.txt\nApproved 12 units.", retrieved)["citations_valid"])

    def test_identical_quote_from_wrong_source_is_rejected(self):
        answer = {"citations": [{"path": "not-retrieved.txt", "quote": "Shared text."}]}
        self.assertFalse(citation_gate(answer, {"not-retrieved.txt": "Shared text."},
            "SOURCE: retrieved.txt\nShared text.", [{"path": "retrieved.txt", "text": "Shared text."}])["citations_valid"])

    def test_quote_from_truncated_source_cannot_borrow_visible_duplicate(self):
        answer = {"citations": [{"path": "b.txt", "quote": "Same quote."}]}
        full = "SOURCE: a.txt\nSame quote.\n\nSOURCE: b.txt\nSame quote."
        self.assertFalse(citation_gate(answer, {"b.txt": "Same quote."}, full[:30],
            [{"path": "a.txt", "text": "Same quote."}, {"path": "b.txt", "text": "Same quote."}], full)["citations_valid"])

    def test_p95_is_nearest_rank_not_mean(self):
        self.assertEqual(percentile(list(range(1, 17)), .95), 16)
        self.assertIsNone(percentile([], .95))

    def test_designated_evidence_coverage_tracks_missing_sources(self):
        case = {"required_evidence": [{"path": "folder/a.txt", "quote": "First."},
                                      {"path": "b.txt", "quote": "Second."}]}
        result = evidence_coverage(case, {"a.txt": "First.", "b.txt": "Second."},
            "SOURCE: a.txt\nFirst.", [{"path": "a.txt", "text": "First."}],
            "SOURCE: a.txt\nFirst.", {"a.txt": "folder/a.txt"})
        self.assertEqual(result, {"designated_evidence_hits": 1, "designated_evidence_total": 2,
                                  "all_designated_evidence_delivered": False})

    def test_native_display_alias_requires_unique_ingestion_chunk_ancestry(self):
        sources = {"folder/a.txt": "Approved 12 units.", "other/b.txt": "Other evidence."}
        docs = [{"id": path, "path": path, "text": text} for path, text in sources.items()]
        statuses = [{"file_path": "a.txt", "chunks_list": ["native-a"]},
                    {"file_path": "b.txt", "chunks_list": ["native-b"]}]
        retrieved = [{"path": "a.txt", "text": sources["folder/a.txt"], "native_chunk_id": "native-a"}]
        def read(path):
            return {"documents": docs} if path.name == "intent.json" else {"native": {"statuses": statuses}}
        with patch("benchmarks.competitors.report.read", side_effect=read):
            catalog, aliases = citation_catalog(Path("unused"), "lightrag", sources, {"sources": retrieved})
            self.assertEqual(aliases, {"a.txt": "folder/a.txt"})
            self.assertTrue(citation_gate({"citations": [{"path": "a.txt", "quote": "Approved 12 units."}]},
                catalog, "SOURCE: a.txt\nApproved 12 units.", retrieved)["citations_valid"])
            retrieved[0]["native_chunk_id"] = "native-b"
            self.assertEqual(citation_catalog(Path("unused"), "lightrag", sources, {"sources": retrieved})[1], {})
            retrieved[0]["native_chunk_id"] = "native-a"
            statuses[1]["file_path"] = "a.txt"
            self.assertEqual(citation_catalog(Path("unused"), "lightrag", sources, {"sources": retrieved})[1], {})

    def test_partial_native_line_obeys_complete_line_deadline(self):
        reader, writer = os.pipe()
        output = os.fdopen(reader, "rb", buffering=0)
        adapter = Adapter.__new__(Adapter)
        adapter.proc = SimpleNamespace(stdin=io.StringIO(), stdout=output, poll=lambda: None)
        adapter.selector = selectors.DefaultSelector()
        adapter.selector.register(output, selectors.EVENT_READ)
        adapter.buffer = b""
        os.write(writer, b'{"status":"ok"')
        errors = []
        def call():
            try:
                adapter.call({"op": "query"}, .02)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=call)
        thread.start()
        thread.join(.2)
        try:
            self.assertFalse(thread.is_alive(), "Partial JSON bypassed the operation deadline")
            self.assertIsInstance(errors[0], TimeoutError)
        finally:
            os.write(writer, b'}\n')
            thread.join(1)
            adapter.selector.close()
            output.close()
            os.close(writer)

    def test_completed_usage_survives_invalid_model_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway = Gateway.__new__(Gateway)
            gateway.root = Path(directory)
            gateway.lock = threading.Lock()
            gateway.failure = None
            gateway.timeout = 2
            def completed(argv, **kwargs):
                kwargs["stdout"].write(json.dumps({"type": "turn.completed", "usage": {
                    "input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 12}}) + "\n")
                Path(argv[argv.index("--output-last-message") + 1]).write_text('{"wrong":true}')
                return SimpleNamespace(returncode=0)
            with patch("benchmarks.competitors.gateway.subprocess.run", side_effect=completed):
                with self.assertRaises(KeyError):
                    gateway.chat({"model": "gpt-6-luna", "messages": [{"role": "user", "content": "test"}]}, "test")
            receipt = json.loads(next(gateway.root.glob("*/receipt.json")).read_text())
            self.assertEqual(receipt["usage"]["input_tokens"] + receipt["usage"]["output_tokens"], 112)
            self.assertTrue(gateway.failure)
            self.assertEqual(len(list(gateway.root.glob("*/failure.json"))), 1)


if __name__ == "__main__":
    unittest.main()
