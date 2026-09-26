# Default local hybrid retrieval

`discover` and MCP `discover` now use the promoted accuracy PoC: independent
literal, Unicode/BM25 lexical, typed identifier, exact BGE-M3 dense, explicit-link
and structural routes, candidate union, RRF, and local BGE cross-encoder ranking
of passages and connected bundles. No generative LLM, subscription, API key or
remote inference is used for graph construction, indexing, or default retrieval.
Embeddings/reranking are local neural models; this is not a model-free system.

PostgreSQL stores immutable snapshot documents, passages, typed edges and their
provenance, retained candidate queues, append-only review receipts, and hash-bound
query results. Transactions publish complete imports and results; advisory locks
suppress duplicate concurrent queries. Both edge directions are indexed. Sources
and preserved original artifacts remain in the content-addressed local vault.
PostgreSQL is a serving copy and durable retrieval-state store; the existing
SQLite ingestion/exhaustive-review engine has not been destructively migrated.

The resident MCP process reuses loaded models and lexical term results. Identical
queries reuse persisted results across processes/restarts. The exact local dense
index and FTS5 lexical index are rebuildable. FTS5 preserves the measured PoC
Unicode/BM25 ranking behavior. A model, index, database or snapshot error is
explicit; there is no silent mechanical, remote-provider or paid fallback.

## Start with Docker Compose

From the repository root (Docker Compose and a CUDA GPU supported by PyTorch):

```sh
python deploy/postgres/configure.py
docker compose up -d --wait postgres
uv sync --project evidencekg --extra hybrid
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python deploy/postgres/manage.py migrate
# Downloads pinned public model weights only; no document uploads.
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python -m evidencekg.hybrid.download_models .evidencekg-private/models
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python -m evidencekg.cli --state .evidencekg prepare-hybrid \
  --models .evidencekg-private/models --database-config .evidencekg-private/postgres/evidencekg.json
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python -m evidencekg.cli --state .evidencekg discover 'Was approval given before the order?'
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python -m evidencekg.cli --state .evidencekg serve
```

Initialize/ingest a vault first using the existing commands if `.evidencekg` does
not exist. `prepare-hybrid` imports the latest captured snapshot, verifies source
text/offsets/locators, builds the index, then publishes `hybrid.json`. Re-run it
explicitly after ingestion changes, dependency/model upgrades, or implementation
changes. `--snapshot` selects a frozen snapshot; `--device cpu` is explicit CPU
operation. Models remain local and missing files cause failure, not downloads.
`--output` and `--hybrid-config` permit separate administrator-selected configs.

On the current workstation the prepared default vault is a copy of the previous
1,000-root-document snapshot (1,506 records including attachments). It reuses the
already downloaded pinned weights at `.evidencekg-private/poc-accuracy-20260926/models`.
The root `.venv/bin/python` contains CUDA/ML dependencies; use that interpreter
for local indexing/discovery here. The installed `.venv/bin/evidencekg` command
is ready to use (`.venv/bin/evidencekg discover "your question"`). Existing
benchmark files and PoC code remain.

The Compose database is PostgreSQL 17, pinned by image digest, exposed only on
`127.0.0.1:55432`; `EVIDENCEKG_PG_PORT` and `EVIDENCEKG_PG_SUBNET` can be set before
initial setup. Credentials are generated once in a private ignored directory;
never commit or display them. Runtime has SELECT/INSERT only, with SQL triggers
also rejecting updates/deletes/truncation. Migrations use a separate administrator.
Secrets are copied into private container tmpfs for the postgres OS user.
A persistent volume, healthcheck, restart policy and bounded logs are configured.
Do not use `docker compose down -v` on retained evidence state.

## Continuation, recovery and backup

The response includes original passages and locators, connected bundles,
`workset_id`, route provenance, omitted candidates/expansions, extraction gaps and
cache/timing information. Continue with:

```sh
PYTHONPATH=evidencekg/src .venv/bin/python -m evidencekg.cli discovery-workset WORKSET_ID --limit 100
# Supply --cursor NEXT_CURSOR until null; MCP exposes discovery_workset as well.
```

Ranking never marks material reviewed. Only an explicit review receipt can do
that. The response is bounded discovery, not exhaustive review. Keep existing
inventory/original-region/locator/exhaustive-review tools for complete accounting.
MCP discover is correctly marked as writing derived cache/queue state, while
original evidence is unchanged. Mechanical compatibility is explicit:
`discover --backend mechanical` or `serve --backend mechanical`.
Existing `discover-accuracy` and `index-discovery` remain explicit legacy opt-ins;
they are not called by the new default.

```sh
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python deploy/postgres/manage.py backup \
  --file .evidencekg-private/backups/knowledge.dump
PYTHONPATH=evidencekg/src evidencekg/.venv/bin/python deploy/postgres/manage.py verify-backup \
  --file .evidencekg-private/backups/knowledge.dump
```

The backup is a consistent PostgreSQL custom-format dump with a SHA-256 sidecar.
Verification restores into a new disposable database and validates snapshot,
workset and cached-result payloads; it never overwrites the running database. Back up the original vault,
model/config identities and credential files separately using protected storage.
The PostgreSQL dump alone does not contain original document binaries. Dense and
lexical indexes can be rebuilt. After a database outage, failed requests remain
errors; completed transactions survive and may be resumed without remote calls.

## Scope of the decision

The stopped benchmark showed more designated reference evidence than mechanical
retrieval (84 vs63 complete sets/100), at higher first-query retrieval cost.
Final supported-answer grading was intentionally stopped by the user; no claim
of measured final-answer accuracy is made. Retained candidate tails are not a
semantic completeness guarantee. The original candidate/rerank/graph budgets are
preserved. Vespa, Neo4j, ANN and extra infrastructure are deferred until measured
capacity/query requirements justify them; no migration alone guarantees accuracy.

Operations follow [Docker Compose readiness](https://docs.docker.com/compose/how-tos/startup-order/)
and [PostgreSQL locking](https://www.postgresql.org/docs/17/explicit-locking.html).
