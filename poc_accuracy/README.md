# Isolated accuracy PoC

This package does not replace EvidenceKG or modify its source vault. It adds
independent literal, lexical, typed-identifier, multilingual dense, explicit-link
and structural routes; reciprocal rank fusion; local cross-encoder scoring;
connected evidence bundles; and a persistent paginated candidate workset.

Canonical originals, source offsets and locators remain unchanged. Search and
model scores are discovery hints. A scored candidate is never automatically
marked reviewed. Unselected candidates, undelivered bundles, extraction gaps and
bounded graph expansions remain explicit. Dense top100, rerank128, graph depth2
and final12 passages are experiment limits, not a recall guarantee. Bundle
windows use maximum relevance; this is not guaranteed joint reasoning across
windows or exhaustive corpus comprehension.

Pinned local models: `BAAI/bge-m3` dense CLS pooling and
`BAAI/bge-reranker-v2-m3`, with1024-token gap-free windows, batch4, CUDA float16.
No remote code is enabled. Full model file hashes and revisions are bound to the
frozen run. No private text is sent to the public model host. Local CUDA/model
failure is explicit; no automatic provider or billing fallback exists.

## Environment and commands

Run from `/home/wenga/src/docworm`. The existing root `.venv` contains local ML
dependencies (Python3.13); `evidencekg/.venv` runs the unchanged benchmark
answer/grading code (Python3.12). No existing dependencies or lockfiles changed.

```sh
# Optional preparation; downloads pinned public model weights only.
.venv/bin/python -m poc_accuracy.download_models .evidencekg-private/poc-accuracy-20260926/models
PYTHONPATH=evidencekg/src .venv/bin/python -m poc_accuracy.runtime

# Freeze before reading outcomes. Never edit frozen code or receipts afterward.
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark freeze
PYTHONPATH=evidencekg/src .venv/bin/python -m poc_accuracy.retrieve_benchmark
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark capture

# Run disjoint partitions0..3; all use the existing dedicated ChatGPT login.
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark answer --partition 0 --partitions 4
# After all answers settle: prepare-grade, then grade --partition0 and1, verify.
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark prepare-grade
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark grade --partition 0
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark grade --partition 1
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.benchmark verify
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m poc_accuracy.report
```

GPU and subscription execution may require normal per-command host permission
when the workspace sandbox hides GPU/network access. Do not weaken the model
worker sandbox. Unknown or failed model calls retain immutable intent/results;
the runner pauses rather than silently retrying them.

## Comparison

Same original100 test questions and frozen1000-root-source corpus. Attachments
increase the inventory to1506 document records. Retrieval only receives the
question-only manifest; gold references are used by capture validation/reporting
and independent grading, never retrieval selection.

Two arms use identical selected passages and the same answer model/prompt/limits:
`poc_hybrid_legacy_context` retains the old answer formatter;
`poc_hybrid_preserved_context` additionally delivers exact locator metadata.
Original20 development answer receipts are retained only for the strict grader's
original120-case batching. Scores report denominator100. The old permissive
per-answer judge is omitted; the unchanged strict completion grader supplies
the outcome. Historical controls remain untouched.

The old strict grader sees cited text without locator metadata. Its scores can
therefore conservatively undercount answers whose support requires those labels.
Designated-reference-set recall is not exhaustive evidence-bundle recall or
human legal adjudication. This is a repeated benchmark, not a fresh holdout.

## Candidate continuation

`Workset.page(workset_id, cursor=..., limit=100)` returns stable, snapshot/query/
model-bound candidates with route provenance and separate review state.
`mark_reviewed` requires a receipt and refuses conflicting concurrent updates.
All state lives under `.evidencekg-private/poc-accuracy-20260926/`; original vault
access uses a stable disposable SQLite copy and hash-verified original artifacts.

## Checks

```sh
PYTHONPATH=.:evidencekg/src evidencekg/.venv/bin/python -m pytest -q poc_accuracy/tests/test_pipeline.py
.venv/bin/python -m unittest poc_accuracy.tests.test_semantic -q
evidencekg/.venv/bin/ruff check --config evidencekg/pyproject.toml poc_accuracy
```

Toy-model tests verify mechanics only. Actual model inference and the full
benchmark are separate evidence recorded with the run.
