# Hybrid default acceptance — 2026-09-26

The default is installed as `.venv/bin/evidencekg`; CLI and MCP discover use local
hybrid retrieval with PostgreSQL. Docker Compose PostgreSQL is healthy at
127.0.0.1:55432. The default vault is a preserved copy of the 1,000-root-document
snapshot: 1,506 document records, 2,516 passages and 2,835 explicit typed links.
The original PoC code and 904 historical baseline receipt hashes are unchanged.
The earlier benchmark was stopped at the user's request; no final grading ran.

## Actual checks

- Real local CUDA query returned eight original passages, each checked against
  the source vault; all 206 candidate rows paginated exactly once and remained
  pending review. Generative model calls: zero.
- Retrieval took 22.904 seconds; resident cached repeat 0.00957 seconds. A new
  CLI process used the persisted result after verifying model files (4.428
  seconds). Initial request including model setup was42.072 seconds. These are
  one smoke query's observations, not throughput or accuracy claims.
- Restarted the new Compose database: the resident client reported its broken
  connection, then recovered on the next request and reused unchanged cached
  evidence. No automatic request replay or provider fallback.
- Installed official-SDK MCP service: hybrid default, correct write annotation,
  concurrent cached results, candidate continuation, foreign snapshot rejection,
  and successful request after the error all passed.
- Fresh, isolated Compose project initialized successfully with secret files and
  runtime password authentication. Runtime is non-owner/non-superuser and has
  SELECT/INSERT only. The disposable project was removed; persistent service
  remains running.
- Consistent PostgreSQL custom-format backup restored into a separate disposable
  database. Snapshot, candidate workset and cached-result hashes validated; the
  live database was not overwritten. Original binaries require a vault backup.
- Re-preparation reuses the completed dense index and atomically republishes a
  configuration while retaining older immutable generations. Final index build:
  105.033 seconds. Earlier development builds are retained separately.
- Full existing suite:326 passed initially; three MCP failures passed when rerun
  outside sandbox IPC restrictions, and the OCR failure passed with the documented
  EVIDENCEKG_TEST_TESSDATA=/tmp/evidencekg-tessdata. No assertion was weakened.
- Eleven PostgreSQL tests passed in a disposable database; ten local-model
  mechanics unittest tests passed. Final focused routing/compatibility checks,
  project Ruff, source distribution and wheel build passed. Wheel includes SQL
  schema and hybrid runtime; installed entrypoint exercised above.
- Independent read-only review identified five issues; all were fixed and exact
  rechecks passed. See ../.swarm/HYBRID-REVIEW.md. Both supervised workers settled
  and were released.

Machine-readable local receipts: `.evidencekg-private/hybrid-acceptance/verification.json`.
Source/operations guide: [HYBRID.md](HYBRID.md).

## Boundaries

PostgreSQL is the serving projection and durable retrieval ledger; original
artifacts and the existing ingestion/exhaustive-review engine remain in the
source vault. Local embeddings and reranking use neural encoders, not generative
LLM calls. Candidate limits and extraction gaps remain explicit. No fresh
accuracy score, exhaustive semantic completeness, legal correctness or managed
production/high-availability claim follows from these checks. Vespa and Neo4j
remain deferred until measured workload requirements justify their added services.
