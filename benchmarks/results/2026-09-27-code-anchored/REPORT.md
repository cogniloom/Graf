# Anchored code relationship replies

Original Graf scored **7/20**; preserving its passages and adding anchored function relationships scored **11/20**, across two fresh executions of the original ten-question source-code benchmark. All 40 answers and 40 grades completed; every failure is retained.

| Execution | Original Graf | Anchored relationships |
| --- | ---: | ---: |
| First | 3/10 | 6/10 |
| Repeat | 4/10 | 5/10 |
| Combined | 7/20 | 11/20 |

These are the original benchmark's pass/fail scores. [All answers and original grades](answers-and-original-scores.json), original summary JSON files and per-case CSV files are retained beside this report.

## Graf change

The opt-in response now retains every original passage in its original order, including Python, other languages and unparseable source. It selects nodes overlapping those exact source intervals, supplies complete functions where space permits, and expands only their direct callers/callees. There is no global replacement-node reranking, second-hop expansion or asserted primary implementation. Node dimensions include enclosing conditions, return/error source and mapping-key reads/writes/deletes. The original AST parser and conservative edge resolver are unchanged from the previous reviewed implementation.

The default serialized response limit is 110,000 bytes. Required original evidence cannot be dropped to make room: an insufficient budget raises an error. Optional node omissions remain explicit. No generative calls or gold answers enter retrieval. The runtime and CLI remain opt-in; ordinary Graf retrieval is unchanged.

This corrects specific selection omissions from the previous experiment: the failed-PDF reply includes ingestion, and the pagination reply includes `API.page`. The timeout reply still spans separate implementations and contains the parser's nested ingestion caller rather than a guaranteed complete downstream data-flow chain. Source preservation does not guarantee the answering model will interpret or quote it correctly.

## Interpretation

The candidate improved in both executions, from 3 to 6 passes and from 4 to 5 passes. Keep it available for further opt-in evaluation; the evidence does not justify a claim of reliable or 100% accuracy.

Timeout handling (`code-03`) and citation-choice resolution (`code-05`) each improved from 0/2 to 2/2. Citation-span identity (`code-06`) and network-policy failure (`code-10`) each regressed from 2/2 to 1/2. Snapshot freezing (`code-01`) and failed-extraction retry (`code-07`) still failed both candidate executions.

Failures were retained under the original gates. Examples include an answer using inline citations but leaving the required citations array empty, an inaccurate source quotation, and extra behavioral claims not supported by the cited source. One snapshot-freezing answer still claimed a retrieval-path evidence gap. Preserving evidence and adding relationships therefore improved the aggregate result but did not ensure complete interpretation or faithful answers.

## Benchmark unchanged

No benchmark implementation, question, gold answer, scoring gate, prompt, answer schema or model setting was changed. The original `luna_graf.py` SHA-256 remains `8beb7b31db663228e53117fa424ee2508e8bf7d3c407c75c51605f6c8ae6c90c`; `native.py` and `corpora.py` match the original frozen hashes. The historical replacement `code_context_eval.py` harness was not used.

Each fresh artifact schedules the original ten source-code rows in their original order. Both original source collections and all 26 original preparation records are retained; only code questions are executed. Candidate code reply records contain the new runtime output. Both versions use GPT-6-luna low and the original GPT-6-astra medium grader through ChatGPT subscription authentication. No failures are retried or excluded. The only execution wrapper is the byte-identical original runner; auxiliary scripts stage and archive reply inputs.

## Verification

- 66 focused tests passed, including unchanged native benchmark tests; Ruff and `git diff --check` passed.
- Real PostgreSQL/CUDA preparation verified identical underlying segment lists and exact retention of original passage text and paths for all ten questions.
- Every emitted quote reconstructed from immutable source offsets/hashes. The actual CLI matched the frozen runtime reply; cache separation and default retrieval were checked.
- Frozen builder bytes remained unchanged throughout preparation and model execution. See [verification](verification.json), [preparation](preparation.json), [provenance](provenance.json) and the [example reply](example-reply.json).

## Per-question results

| Case | Original 1 | Anchored 1 | Original 2 | Anchored 2 |
| --- | --- | --- | --- | --- |
| code-01 | Fail | Fail | Fail | Fail |
| code-02 | Fail | Pass | Pass | Pass |
| code-03 | Fail | Pass | Fail | Pass |
| code-04 | Pass | Fail | Fail | Pass |
| code-05 | Fail | Pass | Fail | Pass |
| code-06 | Pass | Pass | Pass | Fail |
| code-07 | Fail | Fail | Fail | Fail |
| code-08 | Fail | Fail | Fail | Pass |
| code-09 | Fail | Pass | Pass | Fail |
| code-10 | Pass | Pass | Pass | Fail |

## Reproduction and limits

Full raw runs, frozen code and staging/preparation scripts are archived locally at `.evidencekg-benchmarks/code-anchored/`, excluding authentication homes. Run a separately staged artifact with the unchanged original runner:

```sh
PYTHONPATH=/tmp/graf-original-benchmark:/home/wenga/orca/workspaces/Graf/betterluna/evidencekg/src \
  /home/wenga/src/docworm/evidencekg/.venv/bin/python -m benchmarks.luna_graf execute \
  NEW_RUN --home NEW_RUN/codex-home
```

These are ten known development questions with two executions and automated grading, not held-out generalization evidence or a 100% guarantee. Original rubric imperfections remain unchanged. Provider/host caches were not controlled. Original retrieval timings are historical; candidate preparation was measured live, so no latency improvement is claimed. Whole-function context and the byte budget changed together with selection; this experiment does not isolate the effect of each node dimension. Static calls are not exhaustive runtime data flow. Prior results remain untouched. No defaults, commits, pushes or publishing were changed.
