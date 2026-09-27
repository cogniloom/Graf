# LightRAG / Cognee adapter verification

Verified on 2026-09-27 in the `Graf/compare` worktree. Implementation scope was
limited to these two adapters, their tests/docs/dependency snapshots, and ignored
per-adapter environments. No shared manifest/harness, commits, memory writes,
provider credentials, or paid-provider calls were used.

| Check | LightRAG 1.5.7 | Cognee 1.6.1 |
| --- | --- | --- |
| Python 3.12 isolated installation | Passed, fully pinned dependency snapshot | Passed, fully pinned dependency snapshot |
| Real database initialization, no model calls | Passed | Passed |
| Real subscription-backed smoke ingestion | Passed, 1 document | Passed, 1 document |
| Real native context-only query | Passed, 2,694 context characters, 1 file reference | Passed, 3,665 context characters, 31 native node/edge references |
| Expected content retrieved | Mira Chen and Horizon, original source chunk | Mira Chen and Horizon, original passage in native graph context |
| JSONL protocol | 2 successful smoke results; 3 malformed inputs produce 3 errors | 2 successful smoke results; 3 malformed inputs produce 3 errors |

Nine focused regression tests pass:

```sh
python -m unittest benchmarks.competitors.test_lightrag_adapter benchmarks.competitors.test_cognee_adapter -v
```

Both scripts compile. Each worker binds generative calls to `gpt-6-luna` with
high reasoning, loopback gateway, and `benchmark-local`; defaults are configurable
through flags. Native logs are redirected at file-descriptor level to stderr.

Evidence roots: `.evidencekg-benchmarks/competitors/lightrag/` and
`.evidencekg-benchmarks/competitors/cognee/`. Successful live artifacts are
`lightrag/live-smoke.jsonl` and `cognee/live-smoke-v3.jsonl` with matching logs.
Each `init.jsonl` and `protocol-errors.jsonl` records the other real-process gates.
The parent gateway owns raw model usage receipts. Earlier Cognee smoke attempts
are retained: local dependency/capability failure, then a successful native
ingestion whose UUID-keyed response exposed an adapter serialization bug (fixed).
Cognee native-process startup required approved local process access when the
sandboxed protocol check timed out.

The tests verify installation, integration, native evidence, and failure handling
on a tiny smoke input. They do not establish corpus-scale accuracy, latency, or
semantic completeness. Full measured comparisons remain coordinator-owned.
Cognee graph references are retained as native node/edge identities. Original
source citations are now projected only through retrieved native chunk/document
ancestry and exact native source URI manifest keys. This later projection passed
a database-only check against the existing smoke state, recorded in
`cognee/citation-hydration.json`, with the original `adapter-smoke-1.txt` path,
original retrieved text, native chunk/document IDs, and no lineage gaps.
Cognee's version-specific retry/schema transport changes and native preflight
calls are documented in `cognee_adapter.md`; LightRAG settings and source links
are in `lightrag_adapter.md`. No further model calls were made after the lead's
timed-benchmark hold.
