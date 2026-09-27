# Graf and graph retrieval systems

This harness compares actual local Graf, TrustGraph, LightRAG, Cognee and Graphiti
configurations on identical source bytes and questions. It measures a native
index/retrieval pipeline followed by the same final answering model. It is a
configuration comparison, not a claim that the products have interchangeable
features or that one retrieval algorithm is universally better.

## Shared protocol

- Generation: `gpt-6-luna`, reasoning effort `high`, through the existing
  authenticated Codex ChatGPT subscription. The CLI request is recorded; the
  served model identity is not independently attested. No API-key billing,
  paid-overage enablement or alternative-model fallback is used.
- Transport: a local experimental OpenAI-compatible adapter calls Codex for
  each completion and retains raw JSON events, prompt, response and token
  receipts. All generative stages use it, including entity extraction, query
  transformation, final answers and automated judging. Codex instructions and
  process startup contribute overhead; these are not native OpenAI API timings.
  Native `temperature`, `top_p` and `max_tokens` options are retained in request
  evidence but cannot be applied through this CLI transport. Native extraction
  prompts and response validators are preserved.
- Embeddings: competitors use pinned local `all-MiniLM-L6-v2`, 384 dimensions,
  on CPU. Graf uses its product's pinned BGE-M3 embeddings and BGE reranker,
  also on CPU. Different embeddings and ranking policies are part of the tested
  configurations; this does not isolate graph structure or compare equal-sized
  neural models. Model downloads are setup, not query consumption.
- Corpus: the deterministic operational document suite in `benchmarks/corpora.py`.
  The initial comparison uses all 30 files, 14,184 UTF-8 bytes, and all 16 authored
  questions. Source hashes and separate question/gold hashes are frozen. This
  is a small fictional development workload, not customer evidence, a held-out
  evaluation, a source-code benchmark or a scale benchmark.
- Retrieval: native graph/hybrid retrieval, requested limit 12 where supported.
  Products interpret limits differently; native selected counts are retained.
  Source hydration follows native provenance only, never a supplemental lexical
  search or the reference answers. The final prompt receives at most 48,000
  context characters, with truncation recorded. All raw context is retained.
  Context representations remain adapter-specific: notably, LightRAG's native
  data JSON can repeat its separately supplied source chunks. Cognee combines
  original chunks with native graph context; Graphiti combines facts with linked
  episodes. These formats affect final-answer tokens and are not equivalent to
  each product's default answer prompt.
- Answering: one completion with identical instructions/schema, no external
  file tools, source access or gold. Relative paths and verbatim source quotes
  are required. The same prompt asks each system to preserve uncertainty and
  abstain from unsupported conclusions.
- Repetitions: one answering pass per question plus two retrieval-only warm
  passes. Warm passes do not add independent accuracy observations. Systems run
  on a shared workstation. Query measurement runs are sequential; two in-flight
  TrustGraph smoke calls overlapped the beginning of LightRAG indexing (2.799 seconds
  of recorded gateway queue wait). Provider caches, OS caches and unrelated
  workloads are not flushed; the run is not an isolated hardware microbenchmark.
- Failures: immutable operation intents precede work; failures and unknowns
  halt a run, with no automatic repeat that could hide costs. Missing results
  are unavailable, never zero. Smoke/development attempts are distinct from
  measured fresh-state runs.
  Cognee retains its native bounded JSON-validation correction (at most three
  completed-output attempts), all counted. This only handles known completed
  responses; model-client transport retries and model fallback remain disabled.

## Metrics and denominators

Indexing wall time starts immediately before submitting the ingest request and
includes the remaining initialization, source ingestion and native index preparation.
The process has already launched: startup work overlapping intent/receipt setup is
unmeasured, so this is not complete cold-start time. Prior package/container installation
and shared embedding/reranking weight downloads are also excluded. Native tokenizer
downloads inside adapter initialization, where present, remain included. Indexing LLM calls and input/cached/output
tokens are separate from retrieval, final answering and grading. Cached input
is a subset of input; total tokens are input + output, not input + cached +
output. Subscription token usage is not a dollar bill.

Report first-pass retrieval and end-to-end (retrieval + answer) median/p95,
repeated retrieval median/p95, context size/truncation, native LLM calls,
mean query tokens, tokens per strict pass and setup tokens. p95 uses the
nearest-rank definition; 16 questions are too few for a stable tail estimate.

Adapter process-tree RSS is sampled every 250 ms, including child processes.
It excludes databases, the shared embedding/inference gateway, remote inference
and GPU memory. The adapter workdir size includes any source copies but excludes
database volumes, Python environments and shared model weights. These partial
footprints must not be advertised as total product memory/storage requirements.

Strict automated quality requires correct, complete and supported answers,
valid source quotes present both in the original corpus and supplied context,
and the expected abstention behavior. Gold is opened only for judging. Judging
uses the same requested model, with system names removed. Human review is
pending; automated strict passes are not a legal or universal semantic accuracy
certification. The headline denominator is the full planned question set;
unattempted questions and execution failures are reported distinctly.

LightRAG's native result displays basenames rather than original directory paths.
The evaluator resolves these display aliases only through the sealed ingestion
document-to-chunk lists and the actual retrieved native chunk ID. Ambiguous aliases,
unlinked chunks and mismatched source text are rejected. Original answers and prompts
remain unchanged; resolved canonical paths are recorded alongside grading evidence.
This corrects reference interpretation without rerunning or improving an answer.

Designated-evidence coverage separately checks whether every reference quotation
was delivered within the context budget, with the same native source-binding gate.
It is computed after inference and never supplied to retrieval or answering. This
is coverage of authored reference evidence, not exhaustive semantic retrieval recall.

## Reproduction

Adapter-specific files document pinned native dependencies and minimal services.
Use fresh state paths; never reuse smoke data or retry uncertain ingestion.
The gateway needs `sentence-transformers`, the existing subscription login and
the pinned public embedding weights. The controller needs `psutil` and
`jsonschema`; Graf additionally needs its locked hybrid dependencies/models and
an isolated PostgreSQL database. Do not redirect it at an existing product DB.

```sh
PYTHONPATH=. python -m benchmarks.competitors.gateway \
  --output /absolute/new/gateway-evidence --port 18791

# Prepare the common source corpus with the repository's existing builder.
PYTHONPATH=.:evidencekg/src python -m benchmarks.runner prepare \
  /absolute/new/documents-30 --kind documents --count 30

# Each system uses its own interpreter and adapter argv JSON.
PYTHONPATH=.:evidencekg/src python -m benchmarks.competitors.protocol \
  /absolute/documents-30 /absolute/new/run \
  --system lightrag --gateway /absolute/gateway-evidence \
  --command '["/absolute/lightrag/bin/python", "benchmarks/competitors/lightrag_adapter.py", "--workdir", "/absolute/new/index", "--endpoint", "http://127.0.0.1:18791/lightrag/v1"]'

python -m benchmarks.competitors.report grade /absolute/run
python -m benchmarks.competitors.report export /absolute/new/results /absolute/run
python -m benchmarks.competitors.render /absolute/results
```

The exporter verifies run seals, corpus/gold bindings and raw token receipts.
Raw evidence stays under the ignored `.evidencekg-benchmarks/competitors/` tree;
curated summaries, per-query data and charts belong in `benchmarks/results/`.
Checksums detect drift; they do not authenticate an unsigned rewritten archive.
No external publication is performed by these commands.

## TrustGraph query boundary

TrustGraph 2.9.11 exposes native GraphRAG retrieval coupled to synthesis, with no
supported context-only option in the inspected service. Its native context stage
therefore includes one synthesis response before the common final answer; both
model costs are counted. This is an integration limitation of this experiment,
not a claim that an ordinary TrustGraph user must generate two answers. Its
indexing interval also includes a documented 30-second broker-quiescence gate.
See [the native setup and source evidence](trustgraph.md).
