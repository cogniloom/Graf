# LadybugDB integration verification

Implementation location: `/home/wenga/src/graf`. The scoped implementation was transferred from the Orca checkout with a checked three-way merge. Existing automatic CPU/CUDA selection, indexing progress, parser and other concurrent work were retained. Pre-transfer target versions, merge inputs and SHA-256 receipts are in `.swarm/ladybug-integration-transfer/`. The transferred implementation edits were removed from the Orca checkout, restoring its previous dirty contents.

## Implemented

LadybugDB 0.21.0 now supplies the explicit-link graph for newly prepared hybrid serving generations. `prepare-hybrid` bulk-loads Document nodes, resolved ExplicitLink relationships and non-traversable RemainingLink records into a private generation, verifies every native row against the original payload, checkpoints/closes it, hashes the database and atomically publishes the generation. Runtime opens read-only databases, binds graph/source/serving identities, and uses native Cypher incident-edge queries rather than constructing a Python adjacency map. Both directions, source edge order, self-loops, parallel edges, bounded traversal and provenance are retained.

Graph construction uses a 256 MiB buffer pool and two native threads; read queries have a 30-second timeout. Import verification uses one byte per link for duplicate-position tracking. CSV bulk import handles quoted/newline identifiers. Query results and database/connection handles are explicitly closed. Concurrent read-only processes and old readers during new-generation publication were tested.

PostgreSQL still stores verified snapshot records, job/workset state and cached results. SQLite and original artifacts remain the canonical ingestion vault. No existing user database was migrated, restarted or removed. Existing hybrid configurations require `prepare-hybrid` to publish version 2; new app processing already calls preparation. Responses identify `local-hybrid-ladybugdb` and `records_backend: postgresql`.

## Actual checks in the target checkout

| Check | Result |
|---|---|
| Native graph lifecycle, bidirectional bounded-retrieval parity, hybrid default routing | 16 passed |
| Real local CPU BGE-M3/BGE reranker ingestion -> Ladybug traversal -> PostgreSQL workset -> reopen/cache, plus PostgreSQL persistence tests | 12 passed, final run 31.06 seconds |
| Semantic mechanics and existing indexing-progress/device tests with Torch available | 15 passed |
| Initial target graph/core/progress focused check | 23 passed, 4 Torch-dependent checks skipped; those progress checks subsequently passed above |
| Full target Python suites (`evidencekg/tests product/tests`) | 594 passed, 60 skipped, 2 failed |
| Investigation test module rerun | 16 passed |
| Scoped Ruff, `git diff --check`, offline `uv lock --check` | Passed |

The native test uses two synthetic linked documents, one unresolved reference, the existing pinned local model weights, and a newly created disposable PostgreSQL 18.6 container. It performs actual inference and database operations, validates original passage locators, verifies native graph selection and retained worksets, reopens a cached query, and proves a missing graph cannot be hidden by a cached result. No model downloads, provider calls or original user-corpus reads occurred. The interpreter reused available local ML packages alongside the project's installed dependencies; this is source/runtime verification, not a fresh release-package installation test. Dependency versions are recorded in `native.json`.

The full-suite failures were:

- `test_acceptance.py::test_pdf_docx_image_adapters`: OCR literal-text assertion. Reproduced against an isolated snapshot of the target's exact pre-Ladybug source/test files; unrelated to this graph change.
- `test_investigation_service.py::test_followup_is_linked_new_run_and_conservative_erasure_removes_copies`: artifact-parent validation error. Its pre-change isolated run passed, and the current complete investigation module rerun passed all 16 tests. The investigation source changed concurrently during validation; this integration did not edit it. The broad run remains recorded as failed; no clean full-suite claim is made.

Raw results: `focused.log`, `native.log`, `native.json`, `progress.log`, `full-suite.log`, `investigation-rerun.log`, and the two `prechange-*.log` files. Check counts overlap and must not be summed as unique tests.

## Remaining limits

This integrates the explicit-link graph, not every Graf storage function. Native adjacency is queried hop by hop while Python enforces Graf's existing evidence budgets. Passage inventories, typed postings, retained links and lexical/dense ranking still load into memory; this change does not establish million-document end-to-end scalability, incremental graph updates, or distributed operation. The previous standalone multi-million-edge benchmark remains separate evidence.

Independent review was attempted through Orca with requested/effective `gpt-6-astra` medium. Both the implementation worker and the subsequent read-only reviewer failed readiness before task delivery; both were released, and the final worker listing showed zero reclaimable terminals. The lead completed implementation and native checks, but independent review remains uncompleted.
