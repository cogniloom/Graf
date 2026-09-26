# EvidenceKG 0.1.0 release evidence

## Follow-up: semantic assurance and paired evaluation

The 2026-09-25 follow-up adds the workflow documented in [ASSURANCE.md](ASSURANCE.md),
including attributed observations, blind wording review, semantic candidate bundles,
reconciliation, premise challenges and a frozen three-arm evaluation harness.

Final integrated checks: **99 package tests passed in 72.30s**, **77 existing
repository tests passed in 2.654s**, Ruff lint/format passed (37 package Python
files), and wheel/source builds passed. Independent review reproduced and
verified fixes for trusted instruction placement and concurrent observation drift.
It independently passed 88 bounded package tests and six fake-worker tests. A
subsequent citation-boundary review reproduced five issues, independently verified
their fixes, and passed all 99 package tests in 72.22s.

A seeded private-source sample also exposed malformed MIME-header and historical
cross-extraction citation bugs; both were fixed and reproduced in regressions.
The sampled acquisition and model-eligible vaults pass integrity checks. Private
content and reference cases are kept outside public examples. Local retrieval
and user-authorized live tests have not demonstrated a graph advantage. On ten
frozen questions from randomly selected sources, lexical retrieval found labelled
passages in 6/10 cases versus 5/10 with mechanical graph expansion. Citation-valid,
automatically judged correct answers were 3/10 versus 2/10; the same-graph-passages
control scored 3/10. Two separate targeted joint-reading questions scored 1/2 in
all three arms. These small, lead-curated labels and same-model automated judging
are not legal-quality certification, exhaustive recall or a comparison with the
installed embedding-based RAG. Citation failures remain in the denominators.

The explicit-offset review pilot failed on real source quotations. This prompted
immutable citation-ID selection in the subscription review adapter, with complete
raw/resolved artifact lineage and strict verification. Its separate bounded source
pilot validated four source calls without retries; the final ID-selection wording
pilot validated three further calls without retries. This is a usability smoke, not a paired
accuracy improvement or a completed sample review. The frozen benchmark above was
not rerun with a changed protocol to replace its unfavourable results.

The final enhanced-v4 live synthetic run validated **20/20 scheduled tasks**,
retaining three unresolved issues and one human-review flag. Real backup/restore
checks preserved its audit head; terminal resume made no model calls. See
[final live receipt](examples/assurance-live-integration.json). This validates
live citation selection and accounting, not private-case comprehension.

The measurements below describe the preceding foundational release, not a new
throughput or semantic-quality measurement for enhanced review.

## Foundational release

Verified 2026-09-25 on local Linux, Python 3.12.13, official MCP 2.2.0.
The release provides an independent source vault, mechanical graph, stdio MCP
interface and durable exhaustive runner. Existing RAG/lawyer data was not migrated
or analysed. Ingestion and graph construction require no model.

## Checks actually run

| Check | Result and scope |
| --- | --- |
| Package tests | **60 passed in 67.58s**; synthetic fixtures, actual parser processes and official SDK client |
| Existing repository tests | **76 passed in 2.456s** in the original environment |
| Ruff lint/format | Passed; 28 Python files formatted |
| Packaging | Wheel and source distribution built |
| Complete delivery | 1,201/1,201 synthetic sources scheduled and delivered with validated fixture-worker results; SDK separately traversed 1,201 sources and memberships |
| Extraction | Actual PDF, DOCX, image OCR and partly scanned PDF fixtures; explicit Tesseract English data |
| Live model smoke | 3/3 tasks validated: one source unit and two membership packets; gpt-6-astra medium through the existing subscription adapter |
| Recovery | Live backup/restore verified identical snapshot and audit head; terminal resume made zero additional attempts |

The package command was `pytest -q evidencekg/tests --tb=short` with
`EVIDENCEKG_TEST_TESSDATA` configured. Legacy tests used
`python -m unittest discover -s tests -q`. SDK and legacy tests ran through approved
host execution because this session's sandbox prevented AnyIO thread wakeups.

The live fixture was the five-character text `Only.`. It verifies actual model
invocation, citations, persistence, export and recovery, **not legal reasoning
quality**. Earlier runs rejected an invalid citation offset and a provider schema.
Those attempts remain archived. Provider hints were corrected without relaxing
local validation; a new run completed. See [live receipts](examples/live-integration.json)
and the [exported report](examples/live-smoke-report/report.md).

## Acceptance matrix

Numbers correspond to the specification. Coverage assertions use deterministic
fixture workers unless explicitly described as the live smoke above.

| # | Evidence |
| --- | --- |
| 1 | 1,201-source enumeration/scheduling/delivery test and official SDK traversal |
| 2 | Singleton queue and complete lexical/literal retrieval tests |
| 3 | Negation retained; complete paragraph identity distinguishes changed text |
| 4 | Namespace-aware identifier occurrences and exact source spans |
| 5–6 | Ambiguous date/same-name fixture creates surface connections, not identity/event assertions |
| 7 | Late email target resolution and duplicate Message-ID ambiguity |
| 8 | Nested email ancestry, unsupported children, attachment limits and MIME-depth gaps |
| 9 | Six sources retained in one byte group with linear memberships |
| 10 | Actual parser/OCR fixtures retain visual gaps; text-only review cannot close them |
| 11 | Mid-capture mutation rejection and immutable same-path versions |
| 12 | Equivalent mechanical graph projections across arrival orders |
| 13 | Measured 20,000-source key: 20,000 memberships and zero pair edges |
| 14 | Injected document instructions cannot change scope or execute returned commands; parser network denial tested |
| 15 | Wrong identity, malformed output, fabricated quote and invalid spans remain unresolved with retained attempts |
| 16 | Segmentation, cross-boundary literal search, input-budget rejection and complete paginated discovery/neighbours |
| 17 | Expired leases, finite retries, idempotency, frozen adapter identity and terminal resume |
| 18 | Hidden-amendment packet contains original endpoints and reference path; semantic quality remains separate |
| 19 | Backup/restore plus corruption checks for artifacts, graph, queue ledger, findings and audit |
| 20 | Unrelated sources receive source tasks without invented connections |

Additional regressions cover frozen BM25 pagination, reverting the inventory,
filesystem/MIME identity collisions, attachment renames, parser retries, complete
follow-up scheduling and persistence when post-submission scheduling fails.

## Performance

[Raw benchmark](examples/benchmark-20000.json): 20,000 synthetic TXT files,
848,890 source bytes, 43.16 seconds ingestion (463.43 files/second), 80,000 total
postings. One common key produced 20,000 memberships and zero explicit pair edges.
Database: 163,016,704 bytes. Artifacts: 30,587,599 bytes. Posting storage including
occurrence indexes: 737.59 bytes/posting. Storage amplification: **228.07×** for
these tiny files. Median 100-item neighbour-page latency: **0.729 seconds** over
three warm-cache reads.

This is a local synthetic measurement, not representative PDF/OCR throughput or
a million-document capacity promise. Some queries materialize candidate sets;
snapshots retain separate lexical indexes to preserve historical BM25 ordering.
Measure the intended corpus before sizing storage.

## Review and boundaries

An independent read-only Orca reviewer identified seven initial findings and
confirmed their fixes on a second pass. That pass found attachment rename
resolution and incomplete detection of deleted scheduled packets. The lead fixed
both with passing regressions; those final two fixes had no third independent
review. A separate worker hardened the queue; the lead integrated and verified it.
All workers settled and were released.

- Stdio only; optional authenticated HTTP is deferred.
- Exact passages use whitespace-normalized paragraphs. Optional shingles,
  paraphrases and fuzzy identities are not implemented.
- PDF/DOCX/image visual gaps remain explicit. The supplied model adapter consumes
  text; OCR does not establish visual comprehension.
- Original-region access returns preserved artifact/byte ranges, not arbitrary
  newly generated page crops. Additional formats require tested adapters.
- Finite rounds, failed attempts, gaps and unresolved issues qualify status.
  Scheduled completion does not establish semantic completeness.
- Mechanical derivations are reproduced on fixtures and by integrity replay.
  No corpus-wide precision or labelled candidate-recall statistic was measured;
  no end-to-end legal/semantic performance claim is made.
- Acquisition is per-file, not an atomic filesystem snapshot. Captured identity
  is not authenticity. Audit chains need independently retained checkpoints to
  resist administrator rewriting.
- Linux parser network/resource isolation is not a general hostile-file sandbox
  or public upload service.
- No accessible Git metadata/CI was available here. No commit, push or deployment
  was performed. The foundational release did not migrate or analyse the private
  corpus; the later bounded, authorized sample evaluation is described above.
