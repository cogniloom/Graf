# TrustGraph adapter setup and evidence

This adapter runs the **native TrustGraph 2.9.11 text pipeline**, not an isolated
extraction component. The official generated stack contains RabbitMQ, Cassandra,
Qdrant, Garage, API gateway, control/IAM/library/config/flow services, chunking and
extraction processors, embedding/storage processors, native graph RAG, Prometheus
and Loki. UI, web-search demo, binary-document decoding and tabular-only service
groups are omitted for this plain-text benchmark. Fifteen services are configured
(fourteen long-running and the successful Garage initialization job).

Official sources inspected:

- <https://github.com/trustgraph-ai/trustgraph>, source snapshot
  `61524b78b29726ede12d27f41cb41a763a227ed8` (development branch; **not** the deployed
  package version). Critical transport signatures were also inspected in the
  actual pinned 2.9.11 Docker image.
- <https://github.com/trustgraph-ai/trustgraph-config>, generator snapshot
  `bf4dbe0d7155f7ba8da6e3a4c6c49308915a47dd`, npm package 1.0.3.
- Official dialog/config from `https://config-svc.app.trustgraph.ai/api/` and
  deployment generator `https://config-ui.demo.trustgraph.ai/api/generate/docker-compose/2.9`.
  The reviewed request contains only public component names and a 16384 output
  token setting; no corpus, credentials, local paths or private data were sent.

Installed SDK: `trustgraph-base==2.9.11`, Python 3.13.13. Its runtime import needed
an explicit `aiohttp` installation; exact dependencies are pinned in
`trustgraph.requirements.txt`. Native image:
`trustgraph/trustgraph-flow:2.9.11@sha256:41030371979dd80e7fa91f75035229dc16329875fc3f0ed9cdd238fd5cd816a5`.
All backing image digests are pinned in `trustgraph_setup.py`. The official
deployment archive SHA-256 is
`df79201555351fa5d6f7853c924fe63e50c9cd0d10fb58eaf388a90b4b233c57`.

The ignored environment `.evidencekg-benchmarks/competitors/trustgraph/` retains
the official `deploy.zip`, generator inputs, source checkouts, package metadata,
`benchmark-compose.yaml`, native configs under `deploy/`, `stack-config.json`,
image receipts and verification output. The reviewed archive must be retained;
the local setup script rejects a different archive rather than silently deploying
new generated configuration.

```sh
# Rebuild overrides from the retained reviewed official archive (no network I/O).
.evidencekg-benchmarks/competitors/trustgraph/venv/bin/python benchmarks/competitors/trustgraph_setup.py
# Optional setup flags: --endpoint URL --embedding-model NAME --embedding-dims N

# Existing environment already has the following processes/services initialized.
python benchmarks/competitors/trustgraph_proxy.py \
  --socket .evidencekg-benchmarks/competitors/trustgraph/proxy-socket/gateway.sock

.evidencekg-benchmarks/competitors/trustgraph/venv/bin/python benchmarks/competitors/trustgraph_adapter.py \
  --workdir .evidencekg-benchmarks/competitors/trustgraph/NEW_RUN \
  --endpoint http://127.0.0.1:18791/trustgraph/v1
```

The proxy is a separate long-running process. Do not start a second copy while
its socket is active. The adapter never starts/restarts services. Embedding
arguments must match `stack-config.json` and the running transport containers;
changing a setup configuration requires explicitly restarting those containers.
The proxy also accepts `--endpoint URL`; it must match the setup and adapter endpoint. Only one TrustGraph ingest/query worker should run at a time because readiness
checks observe this dedicated stack's queues and processing counters.

All generation uses the common gateway with `gpt-6-luna`, high reasoning, literal
model key `benchmark-local`, SDK retries disabled, and native prompts preserved.
`trustgraph_transport.py` subclasses only native inference transport interfaces:
completion delegates to the native OpenAI processor; embeddings delegate to the
shared local `sentence-transformers/all-MiniLM-L6-v2` endpoint (384 dimensions by
default). The native local FlashRank `ms-marco-MiniLM-L-12-v2` reranker is retained.
Native chunk-size/overlap defaults are 2000/50. Temperature and output token
controls are not applied by the subscription gateway, a shared transport limit.

Container inference reaches a **task-private Unix HTTP socket**, mounted only
in the two inference containers. The proxy permits only TrustGraph chat and
embedding paths, caps requests at 8 MiB/responses at 16 MiB, rejects streaming,
and replaces any incoming authorization with the dummy model key. No network
listener, host networking or firewall change is required. The earlier private
bridge TCP proxy was unreachable from containers and has been stopped. Live
streaming rejection also returned 400 without forwarding any model request.

The compose project owns only `graf-compare-trustgraph-*` resources and subnet
10.231.95.0/24. Published host ports are loopback-only: API 18888, AMQP 15673,
RabbitMQ management 15674 and Prometheus 19093. Native local IAM uses the
coordinator-approved dummy `tg_benchmark-local`; real credentials are never used.
Auto-restart is disabled in compose; native processor/broker recovery behavior
still exists. The model gateway's global uncertain-call halt remains authoritative.

## Native initialization order

TrustGraph 2.9.11 exposes an IAM startup race: the API gateway can request a
signing key after IAM registers consumers but before token-mode bootstrap checks
for empty tables. Lazy key creation then makes bootstrap skip the admin/key
seed, and authentication returns 401. This was reproduced on fresh local
volumes and resolved **without an auth patch**:

1. Start backing services; verify Cassandra with `cqlsh`.
2. Start `control` alone; wait for `IAM: auto-bootstrap complete using
   operator-provided token` in logs.
3. Start API gateway and remaining processors.

Two failed empty setup volumes were preserved, not deleted. The working
Cassandra volume is `graf-compare-trustgraph_cassandra-ordered-start`.

## Native ingestion endpoint

The 2.9.11 SDK `flow.load_text` points at a gateway kind missing from the native
operation registry and returns 404 before inference. The first failed smoke is
retained under `smoke/` with its pending marker. The adapter instead uses the
supported native `library.add_document` plus `library.start_processing`, which
routes `text/plain` internally and emits the source provenance needed by graph
RAG. No gateway registry or authorization checks were changed.

## Operation and evidence boundaries

Each workdir has a distinct native collection and document URIs. Ingest stores
the original via native library API, then invokes native `library.start_processing` (which routes text/plain to the native text-load queue and emits document provenance).
It does not call queue acceptance “complete”: completion requires native chunker,
definition and relationship processing counters to advance, no error/timeout
counter increase, healthy Prometheus targets, and all broker messages acknowledged
for 30 seconds. Indexing wall time includes this fixed 30-second quiescence wait and Prometheus scrape delay. This verifies pipeline completion, not semantic completeness.
An unresolved operation leaves a durable marker and blocks further operations;
there is no automatic document or query retry.

The actual pinned image's `GraphRagQuery` and registered flow operations have no
context-only/skip-synthesis option. `GraphRag.query` unconditionally invokes
`kg-synthesis` before returning; streaming explainability triples do not provide
a supported way to omit that call. Separate `graph-embeddings` and `triples`
services are low-level operations, not a native context-only graph-RAG service.
Pinned source evidence is retained in `native-graph-rag-contract.txt` and
`native-graph-rag-schema.txt` under the ignored environment.

Query uses native `graph_rag`. TrustGraph couples graph traversal with model
concept extraction/scoring/synthesis; this entire native stage must count toward
its measured calls, tokens and latency before the common final answer. Native
`sources` URIs are retained. Only those native source links can trigger library
content hydration; the result exposes `sources:[{path,text,native_id,...}]`, raw
native result, separate source bytes/counts, and an explicitly labeled native
synthesis response. No lexical fallback or invented citation is added.

Verified: real authenticated initialization passed (default flow, 115
native queues, 108 metric series); fourteen services running and Garage init
exited 0; live container-to-Unix-proxy health returned 200 and an unauthorized
path returned 404; eight focused adapter/proxy policy, native-link hydration and
failure tests passed. Native library smoke ingestion advanced chunker,
definition, relationship and graph/document embedding write counters with no
error delta and zero pending broker messages. The same smoke adapter subsequently
completed ingestion and graph-RAG query successfully and exited 0, with its
durable pending marker cleared.

The native query returned `Alice Chen founded Cedar Labs.` and the native source
URI/title for `smoke/trustgraph-002.txt`. Hydration returned exactly the original
81-byte text: its SHA-256 matched the ingestion record, and its native ID matched
the query's returned source URI. Receipts are retained under the ignored
environment in `smoke-native-library.jsonl`, `smoke-native-library.log`,
`smoke-native-library/trustgraph-state.json` and `proxy-unix.log`. The native
response identifies `gpt-6-luna`; gateway receipts remain authoritative for total
model usage.

The coordinator paused this smoke between ingestion and query to serialize model
access, then resumed the same process without reingestion. Its wall time is
therefore not a performance measurement; the coordinator also retained the
disclosed 2.799-second queue overlap with a separate adapter's indexing run.
Full-corpus measured benchmarking remains coordinator-owned. These results
verify this real stack's small ingestion/retrieval/source-hydration journey,
not corpus completeness or equal native query costs across competitors.
