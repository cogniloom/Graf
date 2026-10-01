# Evidence accuracy and user review

Graf separates a source assertion, a graph interpretation, a human review and a model conclusion. Neither a graph edge nor an exact quotation proves truth or semantic support.

Investigations now retain `graf-evidence-v1`: trusted adapter instructions, source qualifications, system-observed coverage, structured conclusions, and material clarification questions. Related passage candidates are included up to a visible bound. The existing 60,000-byte passage budget remains; omitted evidence is disclosed. This does not guarantee that every contrary passage was found.

In Results, Evidence assessment shows supporting and contrary quotations, assumptions, unresolved gaps and affected documents. Clarification questions explain why identity, time, scope or missing evidence could change the answer. Users can answer naturally or state uncertainty. Historical answers remain readable and are identified when no structured assessment was retained.

Open Inspect and review interpretations to inspect retained passages, knowledge annotations, relationships and conclusions. Confirm, reject or clarify with a reason. Subsequent decisions must name the previous review; stale updates fail. Retraction appends a new decision. Originals and automatic extractions are never overwritten. Every effective review has an immutable artifact and an `interpretation_reviewed` ledger event. The ledger retains opaque references, while reasons and corrections are erasable artifacts. Export includes the review history; erasure fences and derivative cleanup apply. Follow-ups receive bounded attributed review assertions, not automatic fact authority. Reviews do not train a model or silently change the collection graph.

## Reproducible evaluation

The nine fictional seed cases in `benchmarks/accuracy-guidance/cases.json` cover conditions, negation, conflicting accounts, identity, time ambiguity, quotations, repeated sources, missing evidence and German text. They are development scenarios, not an independently reviewed or representative accuracy benchmark.

Collect real extraction observations into a **new** private directory:

```sh
python -m evidencekg.investigations.accuracy_eval benchmarks/accuracy-guidance/cases.json \
  --collect /tmp/graf-accuracy-new > /tmp/observations.json
```

Each observation binds the source hash, snapshot, graph hash, actual claim records and extraction status. Collection makes no generative calls. To assess model answers, retain their actual output in each observation's `answer`, record model/effort and prompt/implementation provenance, and then bind the reviewed labels to the updated observation hash.

Human label files contain `corpus_sha` and `cases`. Each reviewed case has `case_id`, `reviewer`, `source_sha`, `observation_sha`, optional `graph_expected` (exact expected claim records), `qualifications` (named boolean assessments), and `answer_ratings`. Available answer ratings are `supported_conclusions`, `contradiction_coverage`, `appropriate_uncertainty`, and `useful_clarification`; use null for unassessed/inapplicable items. The reviewer identity is recorded, not authenticated by this offline utility. Do not represent agent-generated labels as independent human review.

```sh
python -m evidencekg.investigations.accuracy_eval benchmarks/accuracy-guidance/cases.json \
  --predictions /tmp/observations.json --labels /tmp/reviewed-labels.json
```

Reports keep scheduled, recorded, missing, failed, reviewed and unreviewed denominators. Graph exact-record precision/recall and qualification preservation are separate from answer ratings. Unknowns are never zero-quality or successes. `--baseline prior-report.json` requires identical corpus, complete reviewed cohorts and identical assessed items/reference graph labels. It returns both reports without hiding their denominators. Exact-record graph scores are sensitive to the chosen reference representation; they are not a universal measure of graph usefulness.

A successful schema, quotation or regression check establishes implementation behavior, not downstream semantic improvement. Representative source sets, independent labels and real baseline/candidate answer runs remain prerequisites for an accuracy claim.

New follow-ups resolve current review decisions from every ancestor run, so later
retractions and corrections apply without rewriting historical model inputs.
Document-gap guidance combines retained documents (including attachments and
inherited sources) with collection gaps. When the capped collection list leaves
identity overlap unknown, the displayed total is explicitly a lower bound.
Source erasure includes independent runs that retained the source's gap metadata;
restricted paths cannot re-enter through that guidance.
