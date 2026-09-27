# Native Codex matrix

`native.py` runs the existing document and code questions in fresh native Codex
sessions. Codex chooses its own shell/file searches and reads. There is no Graf
retrieval, MCP server, or custom search/read controller. Structured output only
standardizes the final answer and citations for grading.

The matrix comes from `codex debug models`: every reasoning effort on each
selectable (`visibility=list`) model, excluding exactly `gpt-6-astra` high, max,
and ultra. Astra xhigh remains included. Hidden service models are not offered
in the normal Codex model picker and are excluded.

The September 26, 2026 catalog yields seven models, 35 combinations, and 910
question sessions: 16 document questions and 10 code questions per combination.
This is one repetition, with a frozen randomized schedule. It is a development
benchmark, not a statistically replicated or human-certified comparison.

## Configuration and isolation

Use an isolated `CODEX_HOME` referencing the existing subscription login, without
copying credential contents. `--ignore-user-config` alone is insufficient: Codex
can synchronize account-installed plugins into a new home. Both
`features.plugins=false` and `features.remote_plugin=false` are necessary here.
The runner also disables apps, personal skills, project instructions, memory,
and web search. Native shell/file tools and the read-only sandbox stay enabled.
API-key environment variables are removed; authentication must report ChatGPT.
There is no billing fallback, model substitution, or automatic failed-trial retry.

Use normal native session persistence. `--ephemeral` is incompatible with native
delegation in the installed CLI: a controlled calibration succeeded with normal
persistence and failed with a missing-thread error when only `--ephemeral` was
added. The earlier ephemeral matrix is invalid calibration and is excluded.

Run from source-only directories outside the repository hierarchy. Reference
answers and receipts live outside those source roots. This is prompt-level
source isolation, not OS-enforced read confinement: the native read-only sandbox
can read other host paths. Raw commands are retained for inspection.

The code corpus is exported from committed HEAD, preserving bytes and relative
paths under the existing exporter policy (some files gain `.txt`). Dirty files
are not included. Corpus, rubric, and implementation hashes are frozen.

## Commands

Use a Python interpreter with the project's `jsonschema` and test dependencies.
The output directory must be new. Capture the authenticated model catalog before
preparation. Pass absolute output and isolated Codex-home paths when convenient.

```sh
python -m benchmarks.native prepare /tmp/graf-native-run --catalog /path/catalog.json
python -m benchmarks.native run /tmp/graf-native-run --codex-home /path/isolated-home
python -m benchmarks.native grade /tmp/graf-native-run --codex-home /path/isolated-home
python -m benchmarks.native report /tmp/graf-native-run
```

`run` is sequential and resumes past completed successful receipts. A usage reconciliation failure halts its configuration; other failed or
interrupted attempts stop execution and remain visible. Do not remove its
directory to silently retry. Plugin/MCP activity invalidates a trial.

## Metrics

- `summary.csv` and `summary.json`: one row per model, effort, and workload;
  scheduled/attempted/answered/graded counts; strict accuracy; total and median
  elapsed seconds; input, cached-input, and output tokens.
- `per-case.csv`: individual question measurements, status, and grade.
- `grading-usage.json`: separate judging usage and elapsed time.
- `trial-NNNN/`: prompt, schema, command intent, raw JSON events, stderr, answer,
  and elapsed-time/token receipt. A judge subdirectory retains grading evidence.

Usage is summed from native `token_usage_record` entries across the root and all
child sessions, deduplicated by response ID. Root records must reconcile exactly
with the CLI's final event. Native session logs and the reconciliation are retained
per trial; incomplete reconciliation or unfinished children halt the affected
configuration.

Input tokens already include cached tokens. Total tokens are input plus output;
do not add cached input again. Missing usage is unknown, not zero. Reasoning
tokens, when emitted, are retained in raw receipts; output tokens are the primary
reported output measure. CLI token accounting is not a subscription invoice.
Model/effort labels describe requested settings; final events do not independently
attest the served model. Native thread settings are retained for root and children.

Grading uses fresh, anonymous GPT-6-astra medium sessions with the question,
rubric, candidate, and full cited source files. Strict passes require correctness,
completeness, support, exact contiguous source quotes, and correct abstention.
Unfinished grading leaves accuracy blank. Grader costs are separate from answers.
Shared-host load, provider caching, same-family judge bias, authored questions,
and one repetition limit interpretation. No external results are published.

Exact-total policy (user decision, 2026-09-26): a native usage reconciliation
failure halts the affected model/reasoning combination across both workloads.
The failed attempt is retained and never automatically retried. Other combinations
continue. Halted configurations have no full-workload accuracy or token total;
completed individual receipts remain available. Full-workload token totals are
published only after every scheduled receipt has exact usage. Other failures
still block execution. A collection-only amendment binds the original and revised
runner hashes and verifies identical ASTs for generation, configuration, usage
reconciliation, grading, and the answer prompt. Pipeline restarts use exclusive
locking and separate logs.

## Parallel execution

The user requested parallel execution after 29 sequential attempts (27 successful,
2 with incomplete native-child accounting). `native_parallel.py` runs six
model/reasoning pairs concurrently, one question at a time within each pair.
Each slot uses a separate Codex home/session directory and the same subscription
login reference. The unchanged native runner and grader receive only that pair's
schedule; a single coordinator writes reports. No extra MCP is introduced.

`parallel-cohort.json` binds scheduler source, concurrency, and prior trial IDs.
`per-case-execution.csv` labels sequential versus parallel measurements. Parallel
wall times include shared-host/provider contention and should not be interpreted
as isolated latency or directly pooled with the sequential cohort without that
qualification. Answer collection and grading each use the six-slot scheduler;
grading starts after collection. Native child agents may increase the number of
underlying model requests beyond six. Failed attempts are retained without retry.
