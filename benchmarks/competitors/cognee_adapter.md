# Cognee worker

This adapter runs real `cognee==1.6.1` with local SQLite, Ladybug graph storage,
and LanceDB vectors in a separate Python 3.12 environment. The complete installed
dependency snapshot is `cognee-requirements.txt`, including the optional
`transformers` tokenizer required by the chosen embedding model. No Torch model
is loaded by this worker; the parent gateway computes embeddings locally.

```sh
uv venv --python 3.12 .evidencekg-benchmarks/competitors/cognee/venv
uv pip sync --python .evidencekg-benchmarks/competitors/cognee/venv/bin/python benchmarks/competitors/cognee-requirements.txt
.evidencekg-benchmarks/competitors/cognee/venv/bin/python benchmarks/competitors/cognee_adapter.py \
  --workdir .evidencekg-benchmarks/competitors/cognee/run \
  --endpoint http://127.0.0.1:18791/cognee/v1
```

Input/output use one JSON object per line:

```json
{"op":"ingest","documents":[{"id":"doc-1","path":"doc-1.txt","text":"Document contents."}]}
{"op":"query","question":"What does the document say?","limit":12}
```

Ingestion stages the exact input text in hashed `.txt` files under the workdir,
records the input ID/path/content hash in `source-manifest.json`, then calls native
`add` and `cognify`. The manifest is bookkeeping, not evidence that a source was
retrieved. JSON serialization preserves native UUID-keyed pipeline results.
Python and native process logs go to stderr.

The default query is native `GRAPH_COMPLETION` with `only_context=True`,
`verbose=True`, `include_references=True`, and `top_k=limit`. `native` contains
the full returned result, including objects and prompts, and `native_sources`
retains the unmodified native evidence references. `graph_context` retains the
native context separately. No lexical fallback or final-answer generation is
added. `--search-type CHUNKS`
is an explicit alternative native retrieval mode, not an automatic fallback.

For source citation parity, only actually retrieved native `DocumentChunk` and
`TextDocument` nodes are hydrated. A chunk's native `document_id` selects its
parent document; the document's native `_cognee.source_uri` metadata must match
an exact staged-file key in `source-manifest.json`. This produces `sources`
records with original input IDs/paths, native IDs, and original retrieved text.
Missing ancestry remains explicit in `lineage_gaps`; names or text similarity
are never used to guess an attribution. A directly retrieved whole document
can hydrate its staged input only after checking its recorded SHA-256, and is
not added again when a retrieved chunk from that document was already hydrated.

`source_context` formats verified records as `SOURCE: original-relative-path`
followed by the source text. `context` places those records before the separately
labeled native graph context. Graph entity/edge annotations retain their native
identity and are not relabeled as original quotations.

Every generative request is bound to the supplied loopback endpoint, dummy key
`benchmark-local`, `--model` (default `gpt-6-luna`), and `reasoning_effort=high`.
Inherited stage-specific provider routes are removed before import; the workdir
isolates storage and settings. Embeddings use Cognee's `openai_compatible`
transport with string batches. `--embedding-model` defaults to
`sentence-transformers/all-MiniLM-L6-v2`, and `--embedding-dims` to 384.
The parent must provide an authenticated subscription-backed gateway; there is
no paid-provider key in this worker. Public tokenizer assets may download during
the first ingestion. Cognee tracing is disabled.

## Explicit transport adaptations

Cognee 1.6.1's default retry policy has a 240-second minimum retry floor. The
worker changes the native generative and embedding Tenacity policies to one
attempt and SDK/LiteLLM transport retry counts to zero. Structured generation
uses Cognee's own prompted-JSON method and its native bounded validation correction:
at most three completed-output attempts, with validation-error feedback. Every
call is counted; transport exceptions are not caught by that loop. Model fallback
remains disabled. LiteLLM is explicitly
told that `reasoning_effort` is accepted for the new model name; it is not dropped.
These runtime adaptations affect only this worker process and are version-pinned.

Native embedding context-length splitting is retained and recorded in config.
Native `cognify` endpoint connection checks can themselves make model requests;
they use the same fixed transport and appear in the gateway receipts. The native
graph extraction, summarization, storage, and retrieval pipeline remain active.
The gateway's subscription CLI does not apply sampling temperature or token caps.

The measured configuration sets native `data_per_batch=1` and `chunks_per_batch=1`.
Cognee still runs its graph extraction and summarization together for that chunk.
The original default of 20 concurrent data items exceeded LiteLLM's 600-second
client timeout while queued behind the serial subscription bridge. That failed
attempt and all outstanding model receipts are retained separately. A fresh index
is measured only after those calls settle; this transport/setup failure is not a
product accuracy result. No extraction prompt or model retry is changed by the
native batching configuration.

The subsequent single-batch attempt exposed invalid summary JSON: the CLI bridge
passes `response_format` as guidance but does not enforce provider-side schemas.
The adapter therefore selects Cognee's native `_acreate_json_fallback` directly,
the native path for schema-incapable models/transports. This preserves Cognee's
own schema-in-prompt instructions and validation. A one-attempt experiment then
halted on an unescaped control character in completed summary JSON. The final
configuration restores the native three-attempt validation loop; it never edits
model output itself. All three earlier experiments and their receipts remain
retained separately, including calls that completed after a failed client exited.

`--initialize-only` initializes real local databases, saves `adapter-config.json`,
and emits one result without model/embedding calls. Fresh measured workdirs are
required to keep smoke state, caches, and failed attempts out of measurements.
A native operation error is reported and blocks later operations in that process.

## Verification (2026-09-27)

Real installed-library initialization passed. Local failure evidence is preserved:
the first smoke found missing optional tokenizer support and LiteLLM's model
capability check; the second completed native ingestion but exposed UUID-keyed
result serialization, now fixed and regression-tested. The final fresh smoke
passed ingestion and query, yielding 3,665 characters of native graph context
(including the original passage) and 31 native node/edge evidence references.
Both operation responses reported `ok=true`; stdout contained exactly two JSON
lines. These are smoke results, not corpus-scale benchmark measurements.

Durable local evidence is in `.evidencekg-benchmarks/competitors/cognee/`:
`init.jsonl`, `live-smoke-v3.jsonl`, and matching logs; the original failed
`live-smoke` and `live-smoke-v2` outputs/logs are preserved separately. Model usage
receipts belong to the parent gateway. No final answer was generated. Focused
tests cover endpoint rejection, inherited route isolation, input identity
validation, and native UUID-key serialization.

Native-process protocol verification also passed: three malformed operations
produced three JSON errors with no model calls, recorded in `protocol-errors.jsonl`
and `protocol-errors.log`. A sandboxed run timed out during native process startup;
the same check passed with approved local process access.

The later citation projection was checked against this existing native smoke
database without new model or embedding requests. `citation-hydration.json`
records the native chunk ID, native document ID, exact native source URI, original
`adapter-smoke-1.txt` path, original text, and zero lineage gaps. The local
`verify_citation_hydration.py` evidence script forbids model calls during this check.

Official sources consulted:
[PyPI release](https://pypi.org/project/cognee/1.6.1/),
[Cognee repository](https://github.com/topoteretes/cognee),
[configuration API](https://github.com/topoteretes/cognee/blob/main/cognee/api/v1/config/config.py),
[environment template](https://github.com/topoteretes/cognee/blob/main/.env.template).
Native call signatures and retry behavior were checked against the pinned installed source.
