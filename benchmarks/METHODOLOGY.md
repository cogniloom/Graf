# Predeclared comparison protocol

## Question being tested

Does access to Graf reduce the time and model tokens required for a correct, complete, source-supported answer to document and code questions, relative to the same model using ordinary file search and reading?

Graf is useful when its retrieval delivers connected evidence that would otherwise require several searches and reads. The experiment must also expose easy lookups, unsupported questions, missed evidence, and cases where retrieval overhead outweighs any model savings. This mechanism is a hypothesis until the paired measurements establish it.

## Equal treatment

Both arms request `gpt-6-astra`, medium reasoning, the same question, answer schema, maximum response count and per-call timeout. Each question starts fresh. Both can perform case-insensitive literal substring search with pagination and bounded file reads. Search covers every exported file; no hidden top-k selection denies the baseline access to relevant material. Graf adds the `discover` operation with twelve selected passages. It can still use all baseline tools.

Under protocol `graf-context-first-v2`, Graf retrieves the original question before the first model call and delivers that context to the model. That operation is timed and retained as `tool-initial.json`. Both arms then receive the same maximum model-response budget and retain their tools. This models the product's evidence-delivery workflow and guarantees that the treatment actually occurred; it does not force Graf's answer to rely on incorrect retrieved material.

The baseline is a useful controlled file-search agent, not an unrestricted expert developer with a terminal, arbitrary scripts, regex, compiler or language server. A separate native-agent study is needed for that claim. The controller uses fresh structured model calls with accumulated tool history; all repeated context and CLI overhead are charged. This is not a single persistent native Codex thread.

Gold answers and reference citations are not given to the answering model or retrieval engine. Dataset files contain source material only. The corpus generator and reference questions are excluded from the exported code. Tool data is untrusted, quoted evidence; direct shell, network and connector operations are disabled and any such observed tool activity invalidates a call.

The initial tool-availability calibration was stopped when observation showed that the model never invoked Graf discovery. Its raw receipts and exact implementation are preserved under `.evidencekg-benchmarks/docs-core-run` and `protocol-v1`; it is excluded from comparative Graf claims. The corrected protocol is rerun from fresh cases/schedules, without reusing selected successful answers from calibration. This is disclosed development iteration, not an untouched holdout.

## Scope and scales

Publish results separately for documents and code. Record files, non-normalized bytes, physical text lines, extraction records, index size and snapshot identity. A document is not a page, a source file is not an extracted segment, and physical lines are not semantic LOC.

Suggested document scales are 100, 1,000 and 10,000 files. Repeat the same fixed questions across scales to measure distractor sensitivity. Do not count those repetitions as new questions or call templated documents real customer evidence. Large-code claims require a genuinely large, independently selected repository and separately reviewed questions; replicating this repository cannot establish them.

This initial suite has 16 authored document tasks and 10 questions about one real codebase. It is a development benchmark. Neither corpus was independently sampled from customer workload frequency; results cannot be extrapolated to all industries, languages, file formats or repository sizes.

## Measurements

| Measure | Definition |
|---|---|
| Answer completion | An `answer` response reached within the fixed response limit; not necessarily correct |
| Strict automated accuracy | Correct **and** complete **and** supported according to an arm-blinded fresh-context judge, with exact-quote and abstention checks enforced independently; denominator is all scheduled trials once coverage/grading is complete |
| Citation validity | Quoted text occurs exactly in the identified original file; does not establish relevance, entailment or completeness |
| Abstention | Correct on declared unanswerable cases; fails answerable tasks when a justified answer exists |
| Input tokens | Actual CLI `turn.completed.usage.input_tokens`, including cached tokens; summed across every successful call in the trial |
| Cached input | Reported subset of input tokens; never added a second time |
| Output tokens | Actual CLI output usage; reasoning tokens cannot be separately inferred when events do not expose them |
| Elapsed time | Wall time from start of answering through all model calls and tools, including process startup and network wait |
| Tool time | Wall time spent in file search/read or Graf discovery; not inferred model reasoning time |
| Setup | Source export, ingestion and discovery/file-tool initialization separately; hybrid preparation/model downloads must have their own receipt |
| Failures | Failed, timed-out, limited and missing trials remain visible; missing usage is unknown, not zero |

Subscription usage is not a dollar invoice. Do not multiply token usage by invented model prices. If reporting API-equivalent costs later, state the actual published pricing date, cache rates, provider, billing route and whether reasoning is included. Do not switch this run to API billing to obtain a cost figure.

## Order, caches and uncertainty

Use a fixed randomized schedule (`20260926`) over question, repetition and arm. Default to three repetitions; a one-repetition execution is a pilot. Sequential execution within a run limits same-run resource contention. Other workloads on a shared host can still affect timing and must be disclosed.

Do not pretend to flush operating-system, provider-prefix or model caches. Graf's hybrid cache-hit state is retained with each tool response; provider cached tokens are reported separately. First-use and warm reuse answer different questions. Query latency alone cannot establish end-to-end first-use savings.

Summarize median and nearest-rank p95, with trial counts; p95 on a small pilot is unstable. Pair by exact question and repetition, never file-list position. Average repeated differences within each question before a deterministic cluster bootstrap with 2,000 resamples. With fewer than two paired questions, do not report an interval. With interrupted or unknown-usage coverage, withhold the paired difference interval.

The reported answered-pair latency difference is **response latency regardless of answer quality**. A quick incorrect answer is not a win. Compare quality-qualified pairs and cost per strict pass only after semantic grading, while keeping all scheduled failures in the overall accuracy denominator. Do not pool the two corpora to hide regressions.

For repeated use, show measured setup time and query costs together. If baseline query time is `B`, Graf query time `G` and additional Graf setup time `S`, break-even is `ceil(S / (B - G))` queries only when `B > G`, for the same successful workload. If `B <= G`, there is no measured latency break-even. Index refresh cost and storage are additional considerations.

## Grading and publication gate

The automated judge sees the question, reference answer, reference evidence, candidate answer and full cited source documents. It does not receive arm labels, tool history, timing or usage. Grading runs in a fresh context with a separate randomized order; grader tokens/time are not charged to answer generation. Using the same model family can create correlated errors and self-preference, so scores are explicitly **automated**.

Every invalid quoted citation mechanically fails strict grading even if the judge approves. An answerable task requires citations and non-abstention. An unanswerable task requires abstention and a justified explanation. The judge checks material qualifiers, contradictory evidence and complete coverage. Reference answers can also be wrong; disagreements require human review rather than silently rewriting a frozen rubric.

Before a public comparative claim:

1. Run the real hybrid backend with recorded model/index identities and preparation costs.
2. Complete all frozen questions in both arms with repeats and measured usage; disclose every exclusion and error.
3. Have independent reviewers blindly assess factual correctness, completeness, citation support and abstention. Retain adjudication records and disagreements.
4. Replicate on fresh held-out real-world corpora with permission, multiple repositories and representative formats/sizes. Include simple tasks where Graf might not help.
5. Report supported-answer accuracy beside time/tokens and confidence intervals; make the protocol and suitable evidence available for audit.

Permitted wording follows the exact measurement: “On [named corpus], [N] questions × [R] repeats, [version/backend] used [X] tokens and [Y] seconds, with [Z] strict supported answers.” Do not turn this into “Graf is X times faster” or “X% accurate on your documents” without the necessary scope and evidence.
