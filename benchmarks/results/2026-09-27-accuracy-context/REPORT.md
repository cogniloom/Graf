# Accuracy-first code retrieval experiment

The reviewed prototype improves designated source coverage and factual correctness against its fresh original control, but it is **not ready for default adoption**. It does not reach 100% passes and does not establish an improvement over the prior enriched prototype. Keep the evidence-preservation and source-resolution work as an experimental candidate; address implementation mixing and citation reliability, then test fresh held-out questions before promotion.

The final run completed all 40 answers and 40 judgments: **7/20 → 11/20 strict passes**. The separate exploratory run completed another 40 answers and 40 judgments: **9/20 → 12/20**. All 160 calls completed; zero missing/failed operational calls, zero exclusions, unchanged grading. A completed call is not a passing answer.

| Final reviewed experiment | Original Graf | Accuracy prototype |
|---|---:|---:|
| Strict passes | 7/20 (35%) | 11/20 (55%) |
| Judge factual correctness | 12/20 | 17/20 |
| Judge completeness | 11/20 | 18/20 |
| Judge supportedness | 9/20 | 13/20 |
| All three judge criteria | 9/20 | 13/20 |
| Exact citation gate | 16/20 | 16/20 |
| Designated reference excerpts present | 14/22 | 21/22 |
| Mean answer tokens, input plus output | 20,879 | 91,546 |
| Median answer seconds | 13.11 | 18.07 |
| Answers using native tools | 0/20 | 14/20 |

The previous 45KB enriched view contained 17/22 designated excerpts and scored 13/20 strict passes in its own earlier run. The new 11/20 is not a paired comparison against that view; do not interpret cross-run differences as a demonstrated decline or improvement. It does show that more retrieved source has not yet produced a reliably better strict-pass result. The current treatment uses approximately 4.38 times its control’s mean answer tokens; accuracy remains the decision criterion.

## Per-question final strict passes

Each cell has two repetitions, not two independent questions.

| Case | Topic | Original | Final |
|---|---|---:|---:|
| code-01 | Frozen snapshot FTS | 0/2 | 1/2 |
| code-02 | Cursor scope | 1/2 | 1/2 |
| code-03 | Parser timeout and ingestion | 0/2 | 1/2 |
| code-04 | Failed PDF inventory denominator | 2/2 | 1/2 |
| code-05 | Altered quote and citation choices | 0/2 | 0/2 |
| code-06 | Immutable citation IDs | 0/2 | 1/2 |
| code-07 | Extraction retry | 0/2 | 1/2 |
| code-08 | Default snapshot head | 0/2 | 2/2 |
| code-09 | Oversized paginated result | 2/2 | 2/2 |
| code-10 | Seccomp fail-closed behavior | 2/2 | 1/2 |

The pagination regression from the earlier enrichment experiment is absent here, but PDF-inventory and seccomp answers each regress by one pass against the final control. Preserving retrieved evidence is therefore not a guarantee of answer-level non-regression.

## What still fails

- Extra claims remain a major source of failure: unsupported test behavior, an unverified hash algorithm, or calling an implementation “older” without evidence. Bigger packets also leave opportunities to mix independent `evidencekg`, `corpus.py`, and `lawcase_engine.py` behavior.
- Four treatment answers fail exact citation validation. Examples include rewriting README whitespace, inventing an explanatory quotation, and altering a quoted condition. Two of these still pass all three judge criteria, which is why the separate mechanical gate matters.
- One timeout answer abstains on ingestion rather than finding the correct caller. The missing designated excerpt is the `ingest.py` assignment of parser status/warnings. The relative-import resolver fix is correct, but the inner bounded navigation view still does not guarantee that this caller is selected. Complete enclosing functions cannot recover a document that was never selected.
- The failed-PDF answer adds an incorrect causal link from unreadable pages to unknown source count; partial OCR loss alone does not establish unknown descendants. The retry answer mixes cache behavior and misses conditions. These require better relevance and scope handling, not merely more bytes.

The most promising next experiments are package/implementation separation, deliberate caller/consumer expansion when a question spans stages, and stable citation selection with server-side exact quote resolution/validation. Citation handling requires integration with the answering consumer; adding another instruction is not demonstrated to solve it. These are follow-up hypotheses, not measured improvements in this report. No single retrieval score guarantees 100% final-answer passes.

## Change under test

The additive prototype preserves every original hybrid passage, retains selected navigation evidence, merges overlapping source intervals, and expands complete intersecting Python functions. Enclosing class/guard source and preceding exception/match branches are budgeted with the function. It returns exact contiguous source regions with immutable segment references. Optional expansions may be omitted with explicit counts; an undersized budget for original/enriched evidence fails instead of silently dropping passages. Default ceiling: 240,000 canonical UTF-8 bytes.

A small resolver correction recognizes relative module imports such as `from . import parsers`, while retaining conservative shadowing and ambiguity handling. No generated source summary or benchmark answer key participates in selection. The accuracy builder is a Python experimental entry point; it is not wired into the CLI, MCP, or default runtime. Adding hybrid modules changes existing implementation fingerprints and requires the usual explicit hybrid preparation if this worktree is used operationally.

## Design and verification

Two separate paired experiments each use the same ten development questions twice, with 20 original-baseline and 20 treatment answers. Each answer receives a separate anonymous grade. The exploratory run uses the initial additive builder; the final run uses the independently reviewed, corrected builder. The final run is the basis for decisions about current code. Intermediate repaired packets were rebuilt locally but never sent as a separate model experiment.

Both arms retain the exact original answer protocol, native tools, schemas, frozen public 192-file source corpus, and unchanged rubric. Requested answer model: GPT-6-luna, low. Requested judge: GPT-6-astra, medium. Adjacent pairs and subsequent judgments are randomized. Seeds are 20260928 (exploratory) and 20260929 (final). Runs use isolated existing ChatGPT subscription authentication, with no API-key billing or automatic retry of uncertain calls. The two runners overlap in wall time; host/provider load and caches are not controlled.

The independent reviewer reproduced and the lead fixed two scope defects: missing enclosing declarations/guards and missing precedence of earlier exception/match branches. The reviewer then checked the corrected cases and relative-import resolution. Lead validation: 49 focused tests pass, project-configured no-cache Ruff passes, and git diff whitespace checks pass. The lead reproduced the reviewer’s two I001 import findings from the package directory and fixed them; the final no-cache package-directory lint check passes. The initial root-directory check resolved import groups differently. Previous broad-suite inherited OCR/SDK failures are documented in the prior experiment; this follow-up does not claim a fresh full-suite pass.

Both reviewer and lead verified final source regions and original-passage retention. The final packets contain 225 regions and 349 references; all 96 original passages remain. Packet sizes are 72,199–123,414 canonical UTF-8 bytes, with zero omitted function expansions for these ten questions. Exact source reconstruction is not proof of complete semantic context or dynamic reachability.

## Interpretation limits

These are development cases already used to diagnose failures, not held-out questions. Twenty observations per arm represent ten distinct tasks. Reference-excerpt coverage is a post-freeze diagnostic, not the retrieval objective or an input to selection. An excerpt match is not evidence that all relevant source was retrieved or that the final answer is correct.

The treatment changes evidence coverage, grouping, size, and reading instructions together. It does not isolate the contribution of each change. The prior 45KB enrichment experiment is a historical reference, not a randomized third arm in these new runs. A single automated judge and the unchanged, imperfect rubric do not constitute independent ground truth. Known extraction-retry rubric ambiguity remains visible; no exclusions or grading relaxation are introduced.

Source-only working directories and prompt instructions are not OS-level read confinement. Model identities/efforts are requested and checked in CLI receipts, not provider-attested served identities. Token counts include cumulative native turns and cached inputs and are not monetary charges. Estimated end-to-end latency includes historical baseline retrieval, current enrichment, and current answer time; it is not a new randomized live end-to-end retrieval measurement.

## Evidence and reproduction

- [Complete metrics](summary.json)
- [Every answer, grade and native command](answers.json)
- [Trial-level metrics](per-case.csv)
- [Independent review](REVIEW.md)
- [Final run protocol and receipt hashes](final-provenance.json)
- [Exploratory run protocol and receipt hashes](exploratory-provenance.json)
- [Example final evidence packet](example-final-context.json)
- [Preparation/source/model bindings](preparation-provenance.json)
- [Local model identity and environment](model-provenance.json)
- [Exact span verification](span-verification.json)
- [Designated reference-excerpt diagnostic](excerpt-coverage.json)
- [Prior experiment and public-source proof](../2026-09-27-code-context/REPORT.md)
- [Experimental API and harness protocol](../../CODE_CONTEXT.md)

Full raw runs and frozen builder/preparation copies are preserved locally under `.evidencekg-benchmarks/accuracy-context-experiment/`, excluding authentication homes. Original results and frozen source/gold files remain untouched. No commits, pushes, publishing, or default adoption.
