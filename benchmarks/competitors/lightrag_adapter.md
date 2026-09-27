# LightRAG worker

This adapter runs the real `lightrag-hku==1.5.7` Python library in a separate
Python 3.12 environment. The complete installed dependency snapshot is
`lightrag-requirements.txt`; no shared project dependency is changed.

```sh
uv venv --python 3.12 .evidencekg-benchmarks/competitors/lightrag/venv
uv pip sync --python .evidencekg-benchmarks/competitors/lightrag/venv/bin/python benchmarks/competitors/lightrag-requirements.txt
.evidencekg-benchmarks/competitors/lightrag/venv/bin/python benchmarks/competitors/lightrag_adapter.py \
  --workdir .evidencekg-benchmarks/competitors/lightrag/run \
  --endpoint http://127.0.0.1:18791/lightrag/v1
```

Send one JSON object per input line:

```json
{"op":"ingest","documents":[{"id":"doc-1","path":"doc-1.txt","text":"Document contents."}]}
{"op":"query","question":"What does the document say?","limit":12}
```

One JSON result is emitted per operation; Python and native library logs go to
stderr. Results include `ok`, `config`, and `native`; query results also include
`context` and `sources`. Ingestion requires native document statuses to be
`processed`, since LightRAG can return a tracking ID after a failed document.
A failed ingestion or raised native exception blocks subsequent work in that
worker process; a completed native empty/failure query result is retained without
blocking independent later questions.

`context` is the JSON text of LightRAG's native entities, relationships, chunks,
and references. `sources` contains its native reference records; chunks retain
native chunk IDs and the input file path. Generated graph descriptions remain
generated annotations, not verbatim source quotations. There is no added search,
citation inference, or final-answer generation.

The default native retrieval mode is `mix`; `--mode` can select `hybrid`, `local`,
`global`, or `naive`. Both native `top_k` and `chunk_top_k` receive `limit`.
Reranking is disabled because no reranking provider is configured. Fixed-token
chunking uses 1,200 tokens with 100-token overlap. Native extraction, gleaning,
merging, persistence, and LLM caching remain active.

Model transport uses only the supplied HTTP loopback endpoint and dummy key
`benchmark-local`, with HTTPX environment proxies disabled and no automatic
request retries. Every generative request explicitly uses `reasoning_effort=high`
and `--model` (default `gpt-6-luna`). The parent gateway must route these requests
through the authenticated subscription; it must not forward the dummy key to a
paid provider. The worker itself needs no provider credentials.

Embeddings use string batches, `encoding_format=float`, and explicit dimensions.
`--embedding-model` defaults to `sentence-transformers/all-MiniLM-L6-v2` and
`--embedding-dims` defaults to 384. Responses are checked for batch indices,
shape, and finite values. The parent gateway owns local embedding execution.
The native `gpt-4o-mini` tokenizer is only a tokenizer, never a generative model.
Its public encoding may be downloaded on first initialization.

`--initialize-only` creates native storage, saves `adapter-config.json`, emits
one initialization result, and exits without model/embedding calls. Use fresh
workdirs for measured runs; smoke runs and cached native state affect call counts.
The gateway's subscription CLI does not apply sampling temperature or token caps;
that shared transport limitation also applies here.

## Verification (2026-09-27)

- Real installed-library initialization passed.
- Real subscription-backed ingestion and query passed for one 159-character
  smoke document: five entities, four relationships, one original text chunk,
  and a native reference to `adapter-smoke-1.txt`.
- Durable local evidence: `.evidencekg-benchmarks/competitors/lightrag/init.jsonl`,
  `live-smoke.jsonl`, and matching `.log` files. Provider usage receipts belong
  to the parent gateway. These are smoke artifacts, not benchmark measurements.
- Focused contract tests cover endpoint rejection, duplicate document IDs, and
  detection of a swallowed native ingestion failure.
- Native-process protocol verification passed: three malformed operations
  produced three JSON errors with no model calls; `protocol-errors.jsonl` and
  `protocol-errors.log` retain the evidence.

Official sources consulted:
[PyPI release](https://pypi.org/project/lightrag-hku/1.5.7/),
[LightRAG library](https://github.com/HKUDS/LightRAG/blob/main/lightrag/lightrag.py),
[structured retrieval route](https://github.com/HKUDS/LightRAG/blob/main/lightrag/api/routers/query_routes.py).
Implementation signatures were also checked directly in the pinned installed source.
