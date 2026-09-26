# Semantic discovery, wording checks and measured evaluation

These safeguards reduce opportunities to overlook evidence. They cannot certify
that every important meaning or relationship has been found.

## Enhanced review

CLI `review` enables enhanced review by default. Use `--no-enhanced` for the
original contract. MCP clients opt in with `start_review(..., enhanced=true)`;
its default remains compatible with earlier clients. Existing frozen runs keep
their original schema. Changed prompt/adapter contracts require a new run.

Every primary source segment receives two independently scheduled calls:

1. A source review extracts attributed observations: actors, events, obligations,
   conditions, negations, event/document dates, issue keys, categories and search
   terms. Each observation carries exact source citations and worker provenance.
2. A blind wording review sees original evidence without the first review. It
   examines negation, qualifications, exceptions, dates, amounts and scope. Every
   supplied lexical marker needs a cited checklist result; that marker vocabulary
   is explicitly incomplete and does not replace reading the whole passage.

Enhanced packets also include deterministic citation choices: verbatim text
with segment-relative offsets, partitioned into spans of at most 400 characters.
These choices cover all text and select no relevance. Supplemental choices cover
mandatory wording markers crossing a partition boundary. The subscription adapter
now asks the model to select citation IDs and deterministically resolves them to
immutable quotes and offsets. Both raw selections and resolved results are stored,
hash-linked, exported in `citation_receipts.json`, and reproduced by integrity
checks. Unknown IDs, wrong source scope, changed raw duplicate submissions and
malformed results are rejected durably. Ordinary MCP/CLI submissions retain the
strict exact-span contract; no rejected quotation is silently repaired.

The catalog, selection policy and actual provider payload/schema budget are checked
before inference. Enhanced v4 freezes this policy and helper hash; earlier live
attempts remain archived separately and changed contracts require a new run.
Selecting a valid citation does not prove that it supports the associated claim.

Model-proposed issue/event keys bridge different wording and languages. Exact
normalized facet memberships and proposed search phrases generate candidate
bundles with original endpoint text. Posting lookups restrict phrase checks;
shared facets use linear anchored groups rather than document cliques. These
are **attributed candidate connections**, never mechanical facts or merged people.

Every source is reconsidered against discovered issue keys. Later reconciliation
compares the two independent source reviews against the original text. A final
challenge checks each finding, proposed relationship and structured observation
against all cited premises. Unsupported, uncertain, high-impact and conflicting
results remain marked for human review. Differences between blind reviews are
conservatively flagged, including potentially equivalent wording.

The implementation makes one structured semantic discovery wave, alongside the
existing finite follow-up rounds. Later observations remain available in the
discovery preview; this is not an unlimited fixed-point semantic search. All
scheduled work, failed inputs, attempts and unresolved issues remain accounted.
The administrator's queue `kinds` filter supports partial pilot runs; it does not
complete omitted stages and is not exposed to the reviewing model through MCP.

`review_status` reports separate stage counters. `review_evidence` returns
attributed observations, candidate paths and human flags with run/view-bound
keyset cursors. If results change during pagination, the old cursor is rejected.
Exports include complete `assurance.json` beside the original report ledgers.

Independent calls can share model biases. Matching quotations proves quotation
accuracy, not that a claim follows logically or legally. OCR/visual gaps, unknown
identities and unsupported formats still require separate attention.

## Paired evaluation

```bash
evidencekg sample /path/to/source --output /private/new-sample --seed fixed-seed
# Inspect the sample, withhold credentials explicitly, then ingest a separate vault.
evidencekg --state /private/vault benchmark --cases /private/cases.json \
  --output /private/new-experiment --limit 6 \
  --lawcase-worker-state /path/to/existing/subscription-state
```

Sampling uses SHA-256(seed, relative path) ranking without replacement within
declared format strata. It records the full known inventory, exclusions, failed
captures and every selected file's byte hash. It does not silently replace hard
or unsupported examples. A stratified sample does not estimate dossier-wide
prevalence without appropriate weighting.

Reference cases contain `id`, `question`, `expected_answer`, `category` and exact
`evidence_refs`. Freeze and source-check them before retrieval tests. Expected
answers and reference spans never enter retrieval or answer-generation prompts.

The comparison has three arms:

- **baseline:** lexical BM25 discovery with document diversity;
- **graph:** lexical discovery plus mechanical neighbours and, when
  `--enhanced-run-id` is supplied, source-cited model observations;
- **graph_plain_control:** exactly the graph-selected original segments, with
  relationship descriptions removed.

All arms have the same maximum passage count and evidence-byte budget. Graph
selection interleaves two lexical candidates with one distinctive candidate;
actual input sizes are reported rather than assumed identical. This is a specific
lexical baseline, **not a comparison against the existing haiku.rag system or an
embedding-based RAG implementation**. The control separates selection effects
from presenting relationship descriptions; it is not an exhaustive all-source
baseline. Model observation preparation also has a cost, separate from answer
calls, which must be reported for any enhanced comparison.

Answer and judge calls use separate fresh contexts. The judge receives the
question, candidate answer, source-checked reference and original evidence, but
not the arm label, graph reasons or execution order. Automated judging is not
independent human adjudication. The same provider/model can share errors with
the answering model. Report retrieval of labelled spans, citation validity,
scored correctness, failed/unscored cases, elapsed time and input sizes separately.
Missing results stay in the denominator. Small Wilson intervals are descriptive;
they do not establish statistical superiority or full-corpus recall.

Frozen experiment configuration includes snapshot, cases, implementation/model
identity, schemas and the observation set captured once for all arms. Raw intents,
outputs and failures are retained. Resume does not re-call a completed/failed
attempt; retrying requires a distinct experiment, not replacing unfavourable data.

The subscription adapter sends supplied passages to the configured external model.
It uses the existing dedicated login and never enables API billing or tools. Local
storage is not local inference. Review sensitive samples and obtain the necessary
authorization before running. Keep private reports outside source folders and
public package examples.

## Real-source parser and historical-scope fixes

Malformed MIME header bytes are escaped visibly in the extraction with a warning;
original wire bytes remain preserved and the message body is still extracted.
Non-text PDF signature/annotation objects are retained as artifacts instead of
being treated as prose. Failed native PDF text extraction still permits OCR and
annotation extraction. The parser adapter signature is now `native-v2`.

New stores use `mechanical-v2`; explicit link identity includes source spans so
re-extraction cannot append new extraction citations to an old link. Legacy
snapshots retain their IDs and use extraction-scoped citation views. Integrity
checks reproduce both versions and compare the complete global evidence set.
Existing stores can opt into future v2 snapshots with
`configure` using `{"rules_version":"mechanical-v2"}`.
