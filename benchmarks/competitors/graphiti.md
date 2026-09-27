# Graphiti adapter verification

Verified 2026-09-27 in the Graf compare worktree. Graphiti **0.30.2**, Python
**3.13.13**, and Neo4j **5.26.12-community**; exact Python dependency pins are in
`graphiti.requirements.txt`, and the container digest is in `graphiti.compose.yaml`.
The package requires `httpx` at runtime despite the newest OpenAI dependency
installing `httpx2`; `httpx==0.28.1` was explicitly installed and pinned.

Official source: <https://github.com/getzep/graphiti> and its
`graphiti_core/llm_client/openai_generic_client.py`, `embedder/openai.py`,
`graphiti.py`, and `nodes.py`. The installed 0.30.2 source was inspected directly.

```sh
uv venv .evidencekg-benchmarks/competitors/graphiti/venv
uv pip install --python .evidencekg-benchmarks/competitors/graphiti/venv/bin/python -r benchmarks/competitors/graphiti.requirements.txt
docker compose -f benchmarks/competitors/graphiti.compose.yaml up -d --wait
.evidencekg-benchmarks/competitors/graphiti/venv/bin/python benchmarks/competitors/graphiti_adapter.py \
  --workdir .evidencekg-benchmarks/competitors/graphiti/NEW_RUN \
  --endpoint http://127.0.0.1:18791/graphiti/v1
```

The worker consumes one JSON request per line and writes exactly one JSON result
per line; third-party logs are redirected to stderr. Operations are `initialize`,
`ingest` with `{id,path,text}` documents, and `query` with `question` and `limit`.
`--embedding-model` and `--embedding-dims` default to
`sentence-transformers/all-MiniLM-L6-v2` and 384. All generation uses
`gpt-6-luna` with `reasoning_effort=high` and the literal dummy key
`benchmark-local`; only loopback model endpoints are accepted. The shared
subscription gateway does not apply temperature or max-token controls.
The full-corpus run uses the native `structured_output_mode='json_object'`
configuration, which places Graphiti's schema in its own prompt. Provider-side
schema enforcement is unavailable in the subscription bridge. This native
transport setting was selected before the measured run; the earlier smoke used
the default schema-native setting. No generated output is rewritten.

Ingestion uses native `add_episode`, text episodes, a fixed 2026-01-01 UTC
reference time, one document per episode, and no community updates. Sequential
ingestion and `max_coroutines=1` limit concurrency. Native model SDK retries and
the Graphiti generation retry wrapper are disabled. A persisted pending-operation
marker blocks subsequent operations after uncertain failure, including across
worker restarts. Never delete that marker to retry a possibly completed call.
Use one worker per workdir and a fresh workdir per independent corpus/run; each
workdir receives a random native group partition. Database transaction retries
remain native to Neo4j.

Retrieval calls native `Graphiti.search` (edge hybrid RRF). It returns native
edge facts and episode UUIDs, and fetches **only the episodes linked by those
edges**. `context` includes extracted facts and those original episode contents
with their real input paths; fact/source counts and UTF-8 bytes are reported
separately. No lexical search, corpus-wide hydration, generated citation,
LLM reranker, or answer generation is added. Native episode associations are
provenance links, not exact supporting spans or verified entailment.

Actual checks:

- Docker container health passed; all 33 Neo4j indices were ONLINE.
- Native initialization succeeded without a model call. First index creation
  logged equivalent-schema races inside upstream parallel creation; subsequent
  direct `SHOW INDEXES` confirmed actual readiness.
- Real subscription-backed ingestion of an isolated 81-byte source succeeded;
  query returned two native facts and the correct source episode/id/path.
- Hydration recheck returned one 81-byte original source through those native
  episode links, with the expected `SOURCE:` path.
- Three focused unit checks passed: external endpoint rejection, forced model
  policy, and durable no-retry behavior after an uncertain query failure.

Local evidence is under `.evidencekg-benchmarks/competitors/graphiti/`:
`initialization.json`, `initialization.log`, `smoke.jsonl`, `smoke.log`,
`hydration-smoke.jsonl`, and `hydration-smoke.log`. This is a smoke verification,
not the measured corpus benchmark or a claim of Graphiti semantic completeness.
The compose project owns only `graf-compare-graphiti-*` resources, subnet
10.231.94.0/24 and loopback Bolt port 17687. No unrelated resources were changed.
