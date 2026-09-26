# Accuracy-first discovery (experimental)

`discover-accuracy` explicitly sends the question and selected original passages
through the existing ChatGPT-subscription lawyer adapter. It never switches to
API-key billing. Ordinary `discover`, search and read-only MCP tools make no new
model calls. This is a ranked evidence packet for a subsequent answer, not an
exhaustive legal conclusion.

```sh
evidencekg --state /path/to/vault discover-accuracy \
  --question-file question.txt --snapshot SNAPSHOT_ID \
  --output /path/outside/corpus/accuracy-receipts \
  --lawcase-worker-state /path/to/.lawcase
```

The workflow decomposes the question into evidence facets and German/English
search probes. Deterministic lexical retrieval collects whole-document sections
and resolved attachment/reply/reference endpoints. Original lexical seeds give
direct endpoints a bounded admission priority; graph expansion does not recurse.
A model assesses every candidate character in bounded units, by selecting supplied evidence block IDs. The service resolves these to exact
original offsets, including repeated identical text. Selection prioritizes covered question
facets, then source relevance. The final packet retains whole original segments
under 60,000 serialized evidence bytes, with explicit omitted source IDs.

Default limits: 80 candidate segments from at most 16 documents, 12 final
segments, 85,000-byte model request envelopes. These are **previews**, not recall
guarantees. More candidates cost more subscription inference. Plans and model
assessments are attributed hints, never mechanical relationships or evidence.
The source vault is not modified. Exact extraction-artifact verification detects
segment-row drift before a selected source is sent to the model.

For a small diagnostic scope, pass `--scope-documents scope.json`, a JSON list of
up to ten immutable document-version IDs. Every nonempty segment of that explicit
scope enters candidate review, regardless of query or graph features. If candidate
or document limits cannot hold the scope, the request fails explicitly. Final
answer selection remains bounded; inspection of all candidate text is not proof
of comprehension or all combinations. Empty/extraction-gap sources remain in
accounting and cannot be treated as semantically reviewed.

Exact requests, model identity, instructions, schemas and results are checksum
recorded. Successful identical stages can resume from validated receipts. Failed
or interrupted calls require explicit recovery; they are never silently repeated.
No source instructions can change the worker tool policy or execute commands.

Benchmark status and limitations are recorded separately in the local private
accuracy report. Development scores must not be described as a fresh holdout.
Source-span validity, candidate recall and correctly supported answers measure
different properties. Model-written and model-checked references still require
human adjudication for high-stakes use.

## Measured comparison (26 September 2026)

Both methods searched the same frozen 1,000-root-document corpus, including
attachments. They used the same answer model and final evidence budget. A point
requires a supplied, supported answer; abstentions do not count as correct.

| Questions | Previous optimized ranking | Accuracy-first retrieval |
|---|---:|---:|
| 20 fresh questions, separate source families | 13/20 | 19/20 |
| 20 development questions | 15/20 | 20/20 |

The fresh comparison gained six correct answers and lost none. All 24 designated
reference segments were delivered, compared with 17/24 previously. This used 122
additional retrieval-model calls (6.1 per question); it is a whole-workflow
comparison, not a graph-only or equal-compute experiment. References were
model-authored and separately model-checked. This small readable-text sample
does not establish accuracy for the full dossier or unreadable/visual evidence.

The remaining fresh failure involved email To/Cc roles. The frozen benchmark
answer formatter stripped locator context and supplied header values without
field labels. **Answer consumers must retain locators and follow their explicit
continuations**, especially for email fields, tables and page roles. A bounded
locator preview is not complete context. The canonical API retains the labels.
This failure remains in the 19/20 score; it was not rescored after diagnosis.

The final implementation passed 325 tests. Offline replay of all 40 new retrieval
packets preserved the exact evidence selected for answers after the repeated-block
offset correction. The correction has explicit regression coverage. Detailed
private receipts and the CSV report are under
`.evidencekg-private/accuracy-20260926/` at the project root. The earlier
100-question scores (52 without graph, 39 old graph, 69 previous optimized
ranking) remain separate and unchanged.
