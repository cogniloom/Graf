# Low-reasoning code evidence experiment

**Recommendation: retain as an opt-in experiment; do not enable by default yet.** The enriched evidence view improved strict passes from **7/20 to 13/20**, largely through better support and citations. The judge's factual-correctness component improved only from **14/20 to 15/20**. It used **3.26× total answer tokens** and introduced a repeatable pagination regression. This supports further development, not a claim that low-reasoning code comprehension is solved.

No default behavior was enabled, no application MCP/API rollout occurred, and nothing was committed or published. The experimental CLI is available with `discover --code-context`; it emits exactly the enriched view used in the comparison. See [implementation and reproduction](../../CODE_CONTEXT.md).

## Main paired comparison

Ten original development questions, two repetitions, randomized adjacent pairs. GPT-6-luna low answered both arms with the same native tools, base prompt, schema and frozen sources. GPT-6-astra medium judged anonymous answers using the unchanged original rubric and full cited/reference sources. All **40 answers and 40 judgments** completed; zero failed or missing calls and no exclusions.

| Metric | Original Graf packet | Enriched code packet |
|---|---:|---:|
| Strict pass: correct, complete, supported, exact citations, no unjustified abstention | 7/20 (35%) | **13/20 (65%)** |
| Judge accepts correct + complete + supported | 9/20 (45%) | **13/20 (65%)** |
| Judge marks factually correct | 14/20 (70%) | 15/20 (75%) |
| Judge marks complete | 14/20 | 15/20 |
| Judge marks supported | 10/20 | **15/20** |
| Exact citation gate passes | 16/20 | **19/20** |
| Mean total answer tokens, including cached input once | 20,890 | 68,150 |
| Median answering seconds | 13.71 | 19.07 |
| Median estimated retrieval + answering seconds | 32.24 | 40.97 |
| Answers using native file/shell tools | 0/20 | 17/20 |
| Total native command calls | 0 | 32 |
| Mean supplied context bytes, prompt serialization | 43,190 | 44,975 |

Paired trial outcomes: **9 newly passing, 3 newly failing, 4 passing in both, 4 failing in both**. Repetitions of the same ten questions are not twenty independent tasks. These results should not be treated as a general accuracy estimate.

The 3.26× token ratio is not a monetary-cost ratio: cached input differed substantially (79,360 baseline versus 901,376 treatment tokens in total), and these were subscription calls. Exact input/cached/output totals and separate judging usage are in [summary.json](summary.json). No API-key billing was used.

## What changed

The opt-in view searches Python symbols over the complete immutable snapshot, combines lexical/symbol ranking with the **same pinned local BGE reranker as the original run**, and expands statically resolvable dependencies/callers and scoped exact identifiers. It preserves enclosing branch source and segment-relative evidence references. Tests/examples are labelled, missing dependency hints and bounded-coverage limitations remain visible, and the response tells the answering model to inspect missing evidence before answering or abstaining.

The ordinary hybrid result remains the retrieval seed. The enriched view selects different evidence within a 45,000-byte canonical JSON budget; it does **not** guarantee that every original passage survives. This is an important limitation and explains a concrete regression below. Benchmark prompt serialization adds whitespace to canonical JSON, hence a slightly different wire-byte count.

The source-aware packet prompted follow-up file inspection in 17/20 answers. This is an observed behavior change; the experiment does not isolate which of symbol structure, evidence selection, missing-dependency hints or guidance caused each gain. It also does not establish gains for models that lack usable follow-up tools.

Median incremental enrichment preparation was **3.65 seconds** with the existing local CUDA reranker already loaded. Startup and index preparation are separate. No generative model was used to construct evidence or answer hints; benchmark gold was never passed to the builder.

## Per-question strict passes

| Question | Original, out of 2 | Enriched, out of 2 |
|---|---:|---:|
| code-01: snapshot-frozen lexical search | 0 | **2** |
| code-02: cursor snapshot/scope mismatch | 2 | 2 |
| code-03: parser timeout and ingestion result | 0 | **2** |
| code-04: failed PDF and unknown inventory total | 0 | 0 |
| code-05: altered quotes and unknown citation choices | 0 | **1** |
| code-06: immutable citation IDs and duplicates | 0 | **2** |
| code-07: retrying a failed extraction | 1 | 1 |
| code-08: default snapshot head and update | 0 | **1** |
| code-09: oversized paginated result | **2** | 0 |
| code-10: seccomp loading failure | 2 | 2 |

Notable remaining failures:

- **Pagination regression:** the enriched packet emphasized `read_segments` and other budget-related code while dropping the original `API.page` excerpt. One answer described the wrong API; another invented a cursor expression despite doing follow-up reads. Both original-packet answers passed.
- **Failed PDF inventory:** both enriched answers missed the later `unknown_descendants` override and relied on an initial assignment or a separate implementation. The relevant original excerpt was not fully preserved.
- **Extraneous unsupported claims:** one citation-choice answer and one retry answer lost support on additional test/alternate-implementation claims. More context can introduce distracting details.
- **Grading sensitivity:** one head-update answer was judged correct and supported but incomplete for omitting the explicit ordering after `freeze_fts`. The retry reference still overgeneralizes the pending-status condition. These grades are retained, not manually adjusted.

See [every answer, command and rationale](answers.json), [per-case CSV](per-case.csv), and [full trial measurements](trials.json). The acquisition-head [before](example-before.json) and [after](example-after.json) packets provide a concrete view of the presentation change.

## Guidance-only ablation

A separate randomized paired run supplied the **unchanged original passages plus exactly the enriched packet's reading instructions**. One repetition, ten questions, same answering and judging setup. All **20 answers and 20 judgments** completed without failures or exclusions.

| Metric | Original packet | Original + guidance |
|---|---:|---:|
| Strict passes | **6/10 (60%)** | 4/10 (40%) |
| Judge accepts correct + complete + supported | 6/10 | 5/10 |
| Mean total answer tokens | 20,980 | 45,020 |
| Median answering seconds | 15.32 | 15.94 |
| Answers using tools | 0/10 | 3/10 |

Guidance alone showed no benefit in this run. The ablation was chosen after observing increased tool use in early main-run answer traces, before main grading completed. It is a development experiment, not preregistered confirmatory evidence. Its 60% original-packet score versus 35% in the main comparison also illustrates run/grader variability. Do not combine these baselines selectively or claim the instruction text is inherently harmful from ten pairs.

## Verification and evidence boundaries

- **35 focused tests passed**, configured Ruff and whitespace checks passed. After the final CLI view-selection adjustment, the affected tests passed again.
- Initial broader run: **425 passed, 37 skipped, 4 failed**. The same four tests fail on the untouched base commit: OCR extraction plus three MCP SDK initialization timeouts. They were not repaired or weakened as part of this task.
- Final broader rerun before the narrow CLI output adjustment: **428 passed, 37 skipped, 4 explicitly deselected inherited failures**. Skipped PostgreSQL/native-model groups do not count as passes. Final CLI-focused regression checks cover the later two-line output selection.
- **Real local PostgreSQL/CUDA runtime and CLI verified** against an isolated index copy: default and opt-in cache entries remain distinct, original segment IDs are unchanged, both cache hits work, and the CLI emits exactly the runtime's enriched view. This is not a hosted or full application-MCP test.
- **391 emitted source/header spans** across the actual CUDA-ranked experimental packets reconstructed exactly from the frozen segments, with hashes and paths checked; maximum canonical packet size **44,974 bytes**. [Span verification](span-verification.json).
- Independent read-only review reproduced branch/scope/Unicode/budget defects; the lead fixed them and the reviewer rechecked all reported cases. [Review](REVIEW.md). Static navigation is not a complete Python call graph, and unsupported languages retain only bounded original passages.
- All **192/192 source files** were verified byte-for-byte against the anonymously downloadable public `cogniloom/Graf` commit `de5064b252cfbbaeb8e4882ff963c84c3d7b4c98`. [Public-source verification](public-source-verification.json). No private corpus was submitted.
- Local reranker identity exactly matches the original benchmark. [Model provenance](model-provenance.json). Answer session receipts record GPT-6-luna low; these requested/session identities are not independent provider attestations.

The two experiment runners overlapped on the same host/provider. Pairs were sequential and randomized within each run, but caches and service load were not controlled. End-to-end estimates combine historical original retrieval time, measured local enrichment time and current answer time; they are not fresh randomized whole-pipeline latency measurements. The questions were already used during development, automated grading remains imperfect, and no held-out repository or human-adjudicated generalization result is claimed.

Frozen protocol/input/receipt hashes are in [main provenance](enrichment-provenance.json) and [ablation provenance](guidance-provenance.json). Durable local raw evidence is under `.evidencekg-benchmarks/code-context-experiment/` in this worktree; authentication homes were excluded from the archive. Original benchmark sources, rubrics, reports and receipts were preserved.

## Adoption decision

This is a measurable improvement in supported, correctly cited answers, with a substantial token increase and a known retrieval regression. **Do not replace the default packet yet.** Keep the opt-in prototype for further work. The next acceptance gate should preserve the previously useful pagination/PDF evidence, reduce alternate-implementation distractions, and test fresh repository/question sets at a defined token budget. The guidance-only change is not supported for adoption by this experiment.
