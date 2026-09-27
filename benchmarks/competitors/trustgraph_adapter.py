#!/usr/bin/env python3
"""JSON-line adapter for the isolated real TrustGraph 2.9.11 stack."""
import argparse
import base64
import dataclasses
import hashlib
import json
import sys
import time
import uuid
from contextlib import redirect_stdout
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def local_endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path.rstrip('/') not in ('/v1', '/trustgraph/v1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('endpoint must be a loopback HTTP gateway with a /v1 path')
    return value.rstrip('/')


def get_json(url, auth=None):
    headers = {'Authorization': 'Basic ' + base64.b64encode(auth.encode()).decode()} if auth else {}
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


class Worker:
    def __init__(self, args):
        from trustgraph.api import Api
        self.args = args
        self.api = Api(url='http://127.0.0.1:18888/', token='tg_benchmark-local', timeout=1800)
        self.flow = self.api.flow().id('default')
        self.library = self.api.library()
        self.root = Path(args.workdir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'trustgraph-state.json'
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {'collection': 'benchmark-' + uuid.uuid4().hex, 'documents': {}, 'pending': None}
            self.save()

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2) + '\n')
        temporary.replace(self.path)

    def metrics(self):
        expected = {'control', 'ingest', 'embeddings', 'text-completion', 'triples', 'vector-store', 'rag'}
        health = get_json('http://127.0.0.1:19093/api/v1/query?' + urlencode({'query': 'up'}))
        healthy = {item['metric'].get('job') for item in health.get('data', {}).get('result', [])
                   if float(item['value'][1]) == 1}
        if expected - healthy:
            raise RuntimeError('Native processing metrics targets unavailable: ' + ', '.join(sorted(expected - healthy)))
        query = '{__name__=~"tg_consumer_processing_total|tg_downstream_error_total|tg_downstream_timeout_total"}'
        result = get_json('http://127.0.0.1:19093/api/v1/query?' + urlencode({'query': query}))
        if result.get('status') != 'success':
            raise RuntimeError('Native Prometheus metrics unavailable')
        counters = {}
        for item in result['data']['result']:
            metric = item['metric']
            key = json.dumps(metric, sort_keys=True)
            counters[key] = float(item['value'][1])
        if not counters:
            raise RuntimeError('No native TrustGraph processing metrics')
        return counters

    def queues(self):
        return get_json('http://127.0.0.1:15674/api/queues', 'guest:guest')

    def initialize(self):
        flows = self.api.flow().list()
        if 'default' not in flows:
            raise RuntimeError('Default TrustGraph flow is not initialized')
        required = ['chunker', 'kg-extract-definitions', 'kg-extract-relationships',
                    'document-embeddings', 'graph-embeddings', 'triples-write',
                    'doc-embeddings-write', 'graph-embeddings-write', 'graph-rag',
                    'embeddings', 'text-completion', 'text-completion-rag']
        queues = self.queues()
        for processor in required:
            if not any(f'.{processor}--default--default--' in q['name'] and q.get('consumers', 0) > 0 for q in queues):
                raise RuntimeError(f'No active native consumer for {processor}')
        counters = self.metrics()
        return {'ok': True, 'op': 'initialize', 'sdk': version('trustgraph-base'),
                'full_stack_initialized': True, 'flows': flows,
                'native_queue_count': len(queues), 'metric_series': len(counters),
                'collection': self.state['collection'], 'model': 'gpt-6-luna',
                'reasoning_effort': 'high', 'model_calls': 0}

    def wait_for_ingest(self, before, count):
        deadline = time.monotonic() + self.args.ingest_timeout
        quiet_since = None
        while time.monotonic() < deadline:
            queues = self.queues()
            counters = self.metrics()
            errors = {k: v - before.get(k, 0) for k, v in counters.items()
                      if ('"status": "error"' in k or 'tg_downstream_error_total' in k
                          or 'tg_downstream_timeout_total' in k) and v > before.get(k, 0)}
            if errors:
                raise RuntimeError('Native ingestion error counters increased: ' + json.dumps(errors))
            processed = {}
            for k, v in counters.items():
                labels = json.loads(k)
                if labels.get('__name__') == 'tg_consumer_processing_total' and labels.get('status') == 'ok':
                    proc = labels.get('processor', '')
                    processed[proc] = processed.get(proc, 0) + v - before.get(k, 0)
            completed = all(processed.get(p, 0) >= count for p in
                            ('chunker', 'kg-extract-definitions', 'kg-extract-relationships'))
            pending = sum(q.get('messages', 0) for q in queues)
            if completed and pending == 0:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                if time.monotonic() - quiet_since >= 30:
                    return {'criterion': 'native processing counters advanced, no error delta, all broker messages acknowledged for 30 seconds',
                            'processing_deltas': processed, 'queue_messages': pending,
                            'quiet_seconds': 30, 'limitation': 'Pipeline completion evidence, not semantic extraction completeness'}
            else:
                quiet_since = None
            print(f'Waiting for TrustGraph native ingestion: pending={pending}, processing={processed}', file=sys.stderr)
            time.sleep(5)
        raise TimeoutError('Native ingestion did not reach verified quiescence; outcome remains pending, no retry')

    def operate(self, request):
        if self.state['pending']:
            raise RuntimeError('Prior operation failed or is uncertain; no automatic retry; inspect retained state')
        op = request['op']
        if op == 'initialize':
            return self.initialize()
        if op == 'ingest':
            documents = request['documents']
            if not isinstance(documents, list) or not documents:
                raise ValueError('documents must be a nonempty array')
            ids = [d['id'] for d in documents]
            if len(ids) != len(set(ids)) or any(i in self.state['documents'] for i in ids):
                raise ValueError('Document IDs must be unique; ingestion is not retried')
            for doc in documents:
                if not all(isinstance(doc.get(k), str) for k in ('id', 'path', 'text')) or not doc['text'].strip():
                    raise ValueError('Each document needs string id/path and nonempty text')
            self.initialize()
            before = self.metrics()
            self.state['pending'] = {'op': op, 'ids': ids}
            self.save()
            for doc in documents:
                native_id = 'urn:graf-benchmark:' + self.state['collection'] + ':' + hashlib.sha256(doc['id'].encode()).hexdigest()
                self.library.add_document(document=doc['text'].encode(), id=native_id,
                                          metadata=[], title=doc['path'], comments='', kind='text/plain')
                self.library.start_processing(id=native_id + ':processing', document_id=native_id,
                                              flow='default', collection=self.state['collection'])
                self.state['documents'][doc['id']] = {'native_id': native_id, 'path': doc['path'],
                    'sha256': hashlib.sha256(doc['text'].encode()).hexdigest()}
                self.save()
            readiness = self.wait_for_ingest(before, len(documents))
            self.state['pending'] = None
            self.save()
            return {'ok': True, 'op': op, 'documents_ingested': len(documents),
                    'native_documents': self.state['documents'], 'readiness': readiness}
        if op == 'query':
            question, limit = request['question'], request.get('limit', 12)
            if not isinstance(question, str) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ValueError('question must be text and limit 1..100')
            self.state['pending'] = {'op': op, 'question': question}
            self.save()
            result = self.flow.graph_rag(query=question, collection=self.state['collection'],
                                         entity_limit=limit, triple_limit=limit, edge_limit=limit)
            known = {d['native_id']: {'id': ident, **d} for ident, d in self.state['documents'].items()}
            hydrated = []
            for source in result.sources:
                uri = source['uri']
                # Only native returned source URIs can trigger source hydration.
                content = self.library.get_document_content(uri).decode('utf-8')
                hydrated.append({'native_id': uri, 'title': source.get('title', ''),
                                 'id': known.get(uri, {}).get('id'),
                                 'path': known.get(uri, {}).get('path'), 'text': content})
            context = 'NATIVE TRUSTGRAPH GRAPH-RAG RESPONSE (coupled synthesis):\n' + result.text
            for source in hydrated:
                context += '\n\nSOURCE: ' + (source['path'] or source['native_id']) + '\n' + source['text']
            self.state['pending'] = None
            self.save()
            return {'ok': True, 'op': op, 'context': context, 'native_result': dataclasses.asdict(result),
                    'hydrated_sources': hydrated, 'sources': hydrated, 'hydrated_source_count': len(hydrated),
                    'hydrated_source_bytes': sum(len(d['text'].encode()) for d in hydrated),
                    'native_response_bytes': len(result.text.encode()),
                    'retrieval': 'native TrustGraph graph_rag with coupled native synthesis; shared final answer is additional',
                    'citation_limit': 'Native provenance URIs retained; no invented exact-span citations'}
        raise ValueError('Unsupported op: ' + str(op))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--endpoint', required=True, type=local_endpoint)
    parser.add_argument('--embedding-model', default='sentence-transformers/all-MiniLM-L6-v2')
    parser.add_argument('--embedding-dims', default=384, type=int)
    parser.add_argument('--ingest-timeout', default=3600, type=int)
    args = parser.parse_args()
    # Stack transport configuration is explicit, never silently changed by a worker.
    config_path = Path(__file__).resolve().parents[2] / '.evidencekg-benchmarks/competitors/trustgraph/stack-config.json'
    configuration = json.loads(config_path.read_text())
    if args.endpoint != configuration.get('endpoint', 'http://127.0.0.1:18791/trustgraph/v1'):
        parser.error('Endpoint must match the configured TrustGraph stack and Unix proxy')
    if (args.embedding_model != configuration['embedding_model']
            or args.embedding_dims != configuration['embedding_dims']):
        parser.error('Embedding arguments must match the configured TrustGraph stack transport')
    protocol = sys.stdout
    with redirect_stdout(sys.stderr):
        worker = Worker(args)
    for line in sys.stdin:
        try:
            with redirect_stdout(sys.stderr):
                result = worker.operate(json.loads(line))
        except Exception as exc:
            print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
            result = {'ok': False, 'error_type': type(exc).__name__, 'error': str(exc)}
        print(json.dumps(result), file=protocol, flush=True)


if __name__ == '__main__':
    main()
