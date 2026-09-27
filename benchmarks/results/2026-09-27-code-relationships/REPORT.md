# Source-code relationship reply experiment

The relationship reply scored **7/20**, compared with **10/20** for original Graf replies across two fresh executions of the original ten-question source-code benchmark. **Do not adopt this version as the default.** The implementation remains opt-in for review.

| Original benchmark execution | Original Graf reply | Relationship reply |
| --- | ---: | ---: |
| First | 4/10 | 3/10 |
| Repeat | 6/10 | 4/10 |
| Combined | 10/20 | 7/20 |

These are the existing benchmark's pass/fail results, without exclusions or modified scoring. All 40 answers and 40 grades completed. The original runner's summaries and per-case CSV files are retained verbatim beside this report; [answers and scores](answers-and-original-scores.json) retain every failure and original rationale.

## What changed in Graf

The experimental `code_context` reply now presents complete Python function nodes, enclosing conditions, callers/callees, return/error statements, and mapping-key reads/writes/deletes. It supplies source references, separates implementation groups, and explores up to two static relationship hops. Exact shared identifiers are labelled navigation hints, not proven data flow. The default serialized limit is 65,000 bytes.

Selection and presentation both changed: this is a test of the whole relationship reply, not an isolated claim that each individual node dimension helps or hurts. Retrieval uses only source and question data; benchmark answers never enter selection.

## Why it failed

Inspection of the actual replies found selection errors before the answering model ran:

- For parser timeouts (`code-03`), the primary group was the legacy `corpus.py` implementation. The EvidenceKG parser appeared as an alternative without its ingestion caller. Extra relationships then expanded the wrong implementation.
- For failed PDF inventory (`code-04`), the reply selected sampling/reporting inventory functions and omitted the relevant ingestion completeness path.
- For oversized pages (`code-09`), it selected `API.read_segments` and ranking budget helpers instead of `API.page`. The first candidate answer then described the wrong byte-budget behavior.

Exact source in the reply also did not guarantee exact quotations in the answer: the first candidate's snapshot-freezing and acquisition-head answers changed variable names in their citations. These failures remain failed under the original grader.

The next hypothesis is narrower: preserve the original retrieved evidence in the delivered reply, identify the functions containing those passages, and add their directly relevant callers, callees and return consumers before spending space on newly ranked nodes. This has **not** been implemented or validated by this experiment. Merely adding dimensions to a wrongly selected node did not solve the problem.

## Unchanged benchmark and verification

The recovered original `luna_graf.py` is byte-identical to the runner recorded in the original run: SHA-256 `8beb7b31db663228e53117fa424ee2508e8bf7d3c407c75c51605f6c8ae6c90c`. `native.py` and `corpora.py` also match their original frozen hashes. No tracked benchmark implementation was changed. The earlier `code_context_eval.py` experiment runner was not used.

Each new execution manifest selects the original 10 source-code rows, in their original order, from the original 26-question combined workload. Both original source collections and all 26 preparation records were retained. Model settings remain GPT-6-luna low, original native tools/prompt/schema, and GPT-6-astra medium grading with the original citation, abstention, correctness, completeness and support gates. Questions, source bytes, gold and rubric remain unchanged. Only candidate code reply inputs change; run-specific receipts are fresh. Execution uses ChatGPT subscription authentication with no API-key fallback.

- 62 focused tests passed, including existing native benchmark tests; package Ruff and `git diff --check` passed.
- Real PostgreSQL/CUDA preparation checked identical underlying original segment lists for all 10 questions. This does not imply that the new delivered reply preserved every original Python passage.
- All emitted source quotations were reconstructed exactly from immutable segment offsets and hashes. The real CLI's cached reply matched the runtime response used by the benchmark; retrieval made no generative calls. See [verification](verification.json).
- Independent source review closed seven findings with exact regression checks: [review](source-review.md). Original runner provenance was independently checked: [benchmark review](benchmark-provenance-review.md).

## Per-question results

| Case | Original 1 | Relationship 1 | Original 2 | Relationship 2 |
| --- | --- | --- | --- | --- |
| code-01 | Fail | Fail | Fail | Fail |
| code-02 | Pass | Pass | Pass | Fail |
| code-03 | Fail | Fail | Fail | Fail |
| code-04 | Pass | Fail | Pass | Fail |
| code-05 | Fail | Pass | Fail | Pass |
| code-06 | Pass | Fail | Pass | Pass |
| code-07 | Fail | Fail | Fail | Fail |
| code-08 | Fail | Fail | Pass | Fail |
| code-09 | Fail | Fail | Pass | Pass |
| code-10 | Pass | Pass | Pass | Pass |

## Reproduction and limitations

[Provenance](provenance.json) records frozen input, source, runner, preparation, answer and grading artifact hashes. [Preparation](preparation.json) records actual local runtime configuration and model identities. [Example reply](example-reply.json) retains a real candidate response. Full raw runs and the unchanged recovered runner are archived locally under `.evidencekg-benchmarks/code-relationships/`, excluding authentication homes.

Run each independently staged artifact with the original runner:

```sh
PYTHONPATH=/tmp/graf-original-benchmark:/home/wenga/orca/workspaces/Graf/betterluna/evidencekg/src \
  /home/wenga/src/docworm/evidencekg/.venv/bin/python -m benchmarks.luna_graf execute \
  NEW_RUN --home NEW_RUN/codex-home
```

Do not rerun into a completed artifact directory. Staging scripts and exact frozen builders are retained in the local archive; they prepare reply inputs, not replacement execution or grading logic.

Two executions on ten known development questions cannot establish generalization or guarantee 100% passes. Automated grading and known original rubric limitations remain unchanged. Shared provider/host caches were not controlled; no latency or cost improvement is claimed. Original-arm retrieval timings are historical, while candidate preparation timings were measured live. Static Python edges do not prove runtime dispatch or exhaustive dependency coverage. Other languages retain original passages without AST enrichment. No default enablement, commits, pushes or publishing occurred.
