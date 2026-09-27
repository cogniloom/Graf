"""No-model checks for the TrustGraph adapter's readiness and endpoint boundaries."""
import tempfile
import unittest
from types import SimpleNamespace

from benchmarks.competitors.trustgraph_adapter import Worker, local_endpoint


class TrustGraphTests(unittest.TestCase):
    def test_external_endpoint_rejected(self):
        for endpoint in ('https://api.openai.com/v1', 'http://example.org:18791/v1',
                         'http://user:pass@127.0.0.1:18791/v1'):
            with self.assertRaises(ValueError):
                local_endpoint(endpoint)

    def test_processing_errors_never_count_as_complete(self):
        worker = Worker.__new__(Worker)
        worker.args = SimpleNamespace(ingest_timeout=1)
        worker.queues = lambda: []
        worker.metrics = lambda: {'{"__name__": "tg_consumer_processing_total", "status": "error"}': 1}
        with self.assertRaisesRegex(RuntimeError, 'error counters increased'):
            worker.wait_for_ingest({}, 1)

    def test_pending_operation_blocks_query(self):
        worker = Worker.__new__(Worker)
        worker.state = {'pending': {'op': 'ingest'}}
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            worker.operate({'op': 'query', 'question': 'Who?'})


    def test_hydration_follows_only_native_source_links(self):
        from dataclasses import dataclass
        from pathlib import Path
        @dataclass
        class Result:
            text: str
            sources: list
        class Library:
            calls = []
            def get_document_content(self, uri):
                self.calls.append(uri)
                return b'Original source B'
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker.__new__(Worker)
            worker.path = Path(directory) / 'state.json'
            worker.state = {'pending': None, 'collection': 'isolated', 'documents': {
                ident: {'native_id': ident, 'path': ident + '.txt'} for ident in ['A', 'B', 'C']}}
            worker.flow = SimpleNamespace(graph_rag=lambda **kwargs: Result(
                'Native response', [{'uri': 'B', 'title': 'B source'}]))
            worker.library = Library()
            result = worker.operate({'op': 'query', 'question': 'Who?', 'limit': 12})
            self.assertEqual(worker.library.calls, ['B'])
            self.assertEqual(result['sources'][0]['path'], 'B.txt')
            self.assertEqual(result['hydrated_source_count'], 1)
            self.assertNotIn('A.txt', result['context'])
            self.assertNotIn('C.txt', result['context'])


class ProxyBoundaryTests(unittest.TestCase):
    def test_unix_proxy_rejects_unapproved_calls_without_upstream(self):
        import json
        import socket
        import subprocess
        import sys
        import time
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'proxy.sock'
            process = subprocess.Popen([
                sys.executable, str(Path(__file__).with_name('trustgraph_proxy.py')),
                '--socket', str(path), '--endpoint', 'http://127.0.0.1:1/v1',
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 3
                while not path.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(path.exists(), 'test proxy failed to create its socket')
                def request(method, target, body=b'', length=None):
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(2)
                        client.connect(str(path))
                        header = (f'{method} {target} HTTP/1.0\r\nHost: localhost\r\n'
                                  f'Content-Length: {len(body) if length is None else length}\r\n\r\n')
                        client.sendall(header.encode() + body)
                        response = b''
                        while True:
                            part = client.recv(4096)
                            if not part:
                                return response
                            response += part
                self.assertIn(b'200 OK', request('GET', '/health'))
                self.assertIn(b'404 Not Found', request('POST', '/other', b'{}'))
                self.assertIn(b'400 Bad Request', request('POST', '/v1/chat/completions',
                                                        json.dumps({'stream': True}).encode()))
                self.assertIn(b'HTTP/1.0 413 ', request('POST', '/v1/embeddings',
                                                                       length=8 * 1024 * 1024 + 1))
            finally:
                process.terminate()
                process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
