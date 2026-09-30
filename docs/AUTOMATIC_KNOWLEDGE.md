# Automatic knowledge from existing documents

Graf derives a versioned knowledge layer during ingestion without an LLM,
remote inference, document annotation, merge approval, or ongoing human review.
**English and German are the supported languages**, including both within one
document or sentence. Original wording, exact spans and source versions remain
available alongside every interpretation.

The deterministic layer is included in the normal installation. Optional local
CNN parsers add broader name and sentence-structure candidates. Neither layer
claims complete understanding of arbitrary prose or proves real-world truth.

## What gets added

| Record | Automatic output | Boundary |
|---|---|---|
| Source observation | Dates, months, amounts, currencies, typed identifiers and operational vocabulary | A vocabulary match does not establish an event or identity |
| Extracted claim | Approval, cancellation, payment, delivery, rejection, dependency, validity and supersession statements; payment participants and amounts | Bounded clause/event grammar; unreviewed interpretation |
| Predicted connection | Optional EN/DE names and syntactic predicate/dependent candidates | Parser disagreement retained; polarity, attribution, identity and truth unresolved |
| Logical derivation | Calendar ordering between normalized day concepts, with rule/version/premises | Orders calendar days, not event occurrence or validity |
| Potential conflict | Opposite asserted polarities for the same literal scope/day or overlapping stated validity | Identity/scope unresolved; does not select a winner |
| Identity comparison | Explicit names, emails, organisations, addresses and registry IDs compared without merges | Reversible pair decisions; contradictory attributes retained; no transitive identity |
| Source-dependence signal | Identical bytes, repeated claims and near-identical passages | Does not establish copying direction, independence or additional corroboration |
| Uncertain reading | Original audio interval, fidelity diagnostics and audit references | Withheld transcript guesses remain outside searchable prose |

The immutable graph contains source, claim, observation, shared vocabulary,
literal entity surface, identity, uncertainty, conflict and dependence nodes. Typed edges distinguish
source references, candidate mappings, stated event days and rule derivations.
Shared vocabulary is **not** shared real-world entity or event identity.

## English, German and mixed text

All twelve months have shared calendar meanings. `März`, `Maerz`, decomposed
Unicode `März`, and `March` map to `month:3`. Complete dates such as `12. März 2026`,
`12 March 2026`, `March 12, 2026`, and `2026-03-12` map to the same day while
retaining original spelling and offsets. Numerical dates also supply month and
year-month facets for cross-language discovery.

`März 2026` has month precision; it does not acquire an invented event day.
`03/04/2026` retains both March 4 and April 3 readings. German dotted dates in a
recognized German clause use day-month-year; independent date observations keep
unresolved numerical ordering. Invalid dates remain unresolved observations.
`next Friday` and `nächsten Freitag` require an explicit source-time anchor and
stay unresolved; ingestion time is never silently substituted.

Amounts use exact decimal strings, including `1,234.50`, `1.234,50`, and Swiss
`1’234.50`. Ambiguous separators retain alternatives. Explicit currency codes
are preserved; `$` and `£` alone do not choose a currency. Nearby amounts are
not automatically attached to payment events.

Operational concepts normalize across languages, for example
`approval`/`Genehmigung`, `payment`/`Zahlung`, and
`order`/`Bestellung`/`Auftrag`. These are scoped vocabulary aliases, not general
translation or entity merging. Supported claim examples include:

```text
Alice approved order 1847 on 12 March 2026.
Özlem Müller hat die Bestellung 1847 am 12. März 2026 genehmigt.
Die Bestellung 1847 wurde am March 12, 2026 nicht genehmigt.
Order 1847 will be approved only after inspection.
Die Bestellung 1847 muss genehmigt werden, nur nach Prüfung.
Alice paid CHF 50.25 to Bob for invoice INV-1 on 12 March 2026.
Alice zahlte CHF 50.25 an Bob für Rechnung INV-1 am 12. März 2026.
Richtlinie P1 gilt ab 1. März 2026 bis einschließlich 31. März 2026.
Policy P2 supersedes policy P1 from 1 April 2026.
```

| Wording | Representation |
|---|---|
| `was not approved` / `wurde nicht genehmigt` | Negative asserted candidate |
| `must not be approved` / `darf nicht genehmigt werden` | Prohibition |
| `muss nicht genehmigt werden` | Not required; proposition polarity unresolved |
| `may not be approved` / `kann nicht genehmigt werden` | Unresolved modal/negation reading |
| `wird genehmigt` | Unresolved present-passive/future modality |

Questions, reported denials, `nicht nur`, `noch nicht`, `nicht mehr`, unsupported
actors and unsupported grammar do not become asserted operational claims.
Quotation wrappers retain unresolved quoted attribution. Source-document
attribution does not identify an author or speaker. Sentence boundaries preserve
date/amount punctuation and continued double-quotation scopes. Explicit
`Alice said:` / `Alice schrieb:` markers retain their speaker surface. Exact
nearby context accompanies claims because pronouns and surrounding discourse
are still unresolved. Unsupported prose remains searchable, with lexical
observations and optional syntax candidates.

## Structure, identity and temporal scope

DOCX body readings and deleted/inserted revision runs are separate. Unaccepted
changes, comments and notes qualify claims instead of silently becoming current
assertions. Heading paths, table coordinates, explicit table-header markers and
note/comment references remain in locators. Formatting/revision acceptance and
arbitrary Word fields are not fully reconstructed. Raw XML is retained.

Email line-prefix quotation depth, reply markers and HTML blockquotes preserve
quoted scopes. Only authored text may anchor `today/yesterday/tomorrow` or
`heute/gestern/morgen` to the explicitly supplied message day. Inline quotations
and reported speakers do not inherit that date. Ambiguous weekday conventions
remain unresolved. Forwarded-message attribution remains conservative.

Spreadsheet locators retain coordinates, number formats, formulas, available
cached values and first-row header **candidates**. These are not confirmed table
semantics or recalculated values. PDF layout text is supplemented by bounded
text-run matrices and font sizes; coordinates are unvalidated parser estimates,
not guaranteed reading order or table interpretation ([pypdf's limitation](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)).

Identity comparisons use explicit contact/labelled records such as
`Name: Andreas Müller; E-Mail: a@example.test; Firma: Example AG`. German name
spellings can be compared with transliterations; email local-part punctuation
and case are preserved. Matching names plus an explicit matching email or
supported namespaced registry ID produce `same_identified_record` when no
attributes contradict. Other matches stay possible; conflicting fields stay
visible. Quoted/revised support is qualified. No original mention is merged and
no weak pair match becomes a transitive identity decision. Literal actor-name
links are discovery candidates only.

Explicit validity intervals are independent of event days. `through` and
`bis einschließlich` mark an inclusive end; bare `until`/`bis` retain boundary
ambiguity. Supersession requires an explicit statement; a newer source never
automatically overrides an older one. Overlapping incompatible intervals produce
potential conflicts. Conditions and source authority remain relevant.

Near-copy detection uses bounded five-word shingles, bottom-k candidate sketches
and verified shingle Jaccard similarity. Negation changes do not erase either
source or produce extra corroboration. Large/common blocks and omitted candidates
are disclosed. No copying direction or independent-support count is inferred.

## Optional conventional NLP

Install the locked `knowledge-nlp` extra, then restart Graf after any active scan
finishes. Select it alongside the extras already used by that installation, for
example:

```sh
uv sync --project evidencekg --frozen --extra app --extra knowledge-nlp
```

Installations using `hybrid` or `transcription` must retain those extra selections
when synchronizing. The extra pins spaCy, its English and German small CNN model
wheels, and Lingua. Ingestion never downloads models. Missing optional models
produce a coverage gap while deterministic EN/DE extraction continues.

Both parsers are eligible for each passage; a document-wide language guess never
excludes mixed-language evidence. Lingua hints are relative only to English and
German, not calibrated language probabilities or proof that arbitrary foreign
text belongs to either language. Raw scores are explicitly rounded to eight
decimal places for reproducible storage. Parser identities include package
versions and model payload hashes. Language/configuration checks reject
transformer/LLM pipelines.

Names remain mentions, including competing person/organization/place readings.
Syntax candidates retain parser-specific lemmas, dependencies and exact offsets.
They do not establish asserted predicates or resolved identities. A model can
mistake a month name for a person; that prediction stays distinguishable from
the calendar observation.

## Uncertainty and provenance

Transcription fidelity, extraction, identity matching, retrieval relevance and
truth are separate. Calibrated probabilities remain `null` without relevant
reference evidence. Decoder/language scores are not renamed as correctness
probabilities. Document quality is not averaged into a blanket truth score.

Original audio, locators and available decoder diagnostics remain source-bound.
Speech withheld by the transcription adapter stays withheld. This layer does
not invent alternate transcripts or expose withheld wording as claims. Date and
amount alternatives are competing readings of one span, not independent facts.
Repeated wording, duplicate recordings and graph paths do not multiply support
or manufacture calibrated confidence.

Uncertain ASR intervals may be decoded again with a different beam setting using
the same local model. Retry input is bounded to 30 seconds total and 10 seconds
per interval. Actual alternative outputs remain audit-only, share the original
recording/model dependency, and never release withheld wording by voting.
Low-score negation/number spans are flagged by category and timestamp.

`python -m evidencekg.knowledge_calibration references.json output.json` fits
isotonic fidelity calibration from supplied independent reference outcomes and
evaluates disjoint validation source families. Input has `training`, `validation`
and `scope`; each row has `raw_score`, boolean `correct`, and `source_family`.
Scope requires `model_sha256`, `language` (`en` or `de`), `input_domain`, and
`probability_target: "transcription_fidelity"`. The output retains reference
hashes, sample counts, range coverage and held-out Brier scores.

Configure `transcription_calibration` with the absolute output path,
`transcription_input_domain`, and an explicit matching `transcription_language`
to annotate later ASR. Scope/range mismatches, missing references, or worsened
validation scores retain an unknown probability. Malformed models are rejected.
Calibration never changes withholding or estimates claim truth. No production
reference dataset was supplied or invented; fixture tests establish plumbing,
not calibration accuracy on the user's recordings.

Each record binds its extraction, parser artifact, original blob and exact
source-segment references. Each generation binds code, vocabulary, optional
model identity and graph artifact. Calendar proofs retain the named rule/version,
premise concept IDs and limited conclusion. Adjacent calendar-day ordering is
stored; longer order follows by traversal without quadratic day-pair expansion.

## Evidence sets and LLM access

The read-only MCP `knowledge_query`, CLI `knowledge` command, and authenticated
`GET /api/knowledge` share a query contract. A useful request is:

```json
{
  "snapshot_id": "N…",
  "kind": "evidence_set",
  "entity": "Bestellung:1847",
  "predicate": "approval",
  "applicable_on": "12. März 2026"
}
```

Evidence sets preserve **all stored claims for the literal entity/predicate**, plus
explicit incoming supersession statements:
positive, negative, planned, qualified, quoted, other-day and unknown-date claims.
The requested day labels temporal roles (`before`, `same`, `after`, `unknown`);
it does not hide other dates. Role counts, shared-source/conflict groups and
continuations remain visible. This is possible-identity context, not aggregation
over confirmed real-world identities.

For an exact stored event-day filter, use `kind="claim"`; that excludes unknown
days and is not an ongoing policy-validity query. `kind="observation"` supports
`concept="month:3"` or `text="März"`/`text="March"`. Text filtering uses any
recognized vocabulary concept, or casefold literal matching when none is
recognized; it is not arbitrary semantic translation. `segment_id` enumerates
retained claim/observation annotations for a particular original passage.

`valid_at="20. März 2026"` filters claim queries by explicit stated validity,
including unresolved end boundaries. On evidence sets it labels validity roles
without hiding unknown/other-period claims. `kind="identity"` exposes reversible
comparisons and supporting records; `kind="uncertainty"` exposes unclear audio
intervals and audit references. `segment_id` also works for uncertainty records.
With `kind="edge"`, it enumerates incident relationships and incoming edges
sharing their target nodes, with related source locators. Hybrid bundles supply
this paginated continuation even for contact-only passages without claim keys.

```sh
evidencekg --state /path/to/state ingest
evidencekg --state /path/to/state knowledge --kind evidence_set --entity Bestellung:1847
evidencekg --state /path/to/state knowledge --kind observation --text March
evidencekg --state /path/to/state knowledge --kind conflict
evidencekg --state /path/to/state knowledge --kind edge
evidencekg --state /path/to/state verify
```

Follow every `next_cursor` for the full stored set. Cursors bind snapshot, filters,
normalizer and normalized query concepts. Frozen claims retain their original
concepts; new rules do not silently reinterpret historical results. Receipts
include counts, omissions, gaps, graph identity, snapshot recording time and
limitations. **All stored matches** is the exhaustive contract, not every
relevant meaning in arbitrary documents.

Hybrid discovery has bilingual-concept and typed claim-context routes. Bundles
favor different polarity, qualification, modality and time roles alongside
relevant original passages. Candidate, bundle, annotation and projection limits
are disclosed with evidence-set continuations. Shared-feature discovery paths do
not become factual evidence paths. No learned ranker or generative relevance
judge was added, and no downstream LLM answer-quality gain is claimed.
Typed candidate traversal also follows supersession, identity comparisons,
literal actor records, potential conflicts and possible reproduced passages.
Their uncertain relationship semantics and omitted memberships remain visible.
Relationship continuations enumerate all stored matches for the passage scope;
shared-target edges may include broader discovery context, not just evidence.

The detailed Workspace Graph displays distinct `knowledge_*` records. Selecting
a node or knowledge edge loads its full record, uncertainty and source refs.
Graph summaries stay compact; detail reads are separately bounded and fenced to
the selected snapshot. The simpler `/api/graph` remains a document view. The
knowledge graph lives in the immutable vault rather than changing Ladybug's
mechanical-link schema. Compact annotations also travel through the existing
PostgreSQL source projection and research context.

## Lifecycle, limits and checks

Within a vault, unchanged extractions reuse enrichment only when the full
implementation/model signature matches. Rules rederive enrichment even when
source bytes are unchanged. Changed/deleted premises affect later snapshots;
historical generations retain their graph and evidence. `verify` checks hashes,
endpoints and exact spans, and repeats derivation when the original signature
is installed.

Knowledge artifacts use versioned, bounded zlib compression; older uncompressed
generations remain readable. Paragraph analyses are content/configuration-bound
and rebound to each extraction's exact source IDs and offsets. The app maintains
a private shared paragraph cache across isolated publication generations. Cached
text is retained under the workspace home; it is not a public evidence endpoint.
Unchanged graph inputs reuse their frozen graph artifact. Verification bypasses
the paragraph cache when recomputing current rules. Changed graph inputs still
rebuild the bounded cross-document projection; there is no general incremental
multi-proof Datalog engine.

After installing code/models and restarting, the app queues one new publication
when the settled publication used different knowledge rules. Active scans are
not superseded just for an upgrade. Requested parent and actual worker signatures
are retained separately, preventing hot package replacement from causing an
endless rebuild loop. A mismatch produces a restart notice. Hot code/model
replacement is not a supported substitute for restarting. Dashboard rebuilds
retain the existing full publication lifecycle; cross-generation reuse covers
knowledge analysis, not the format parsers or embedding generation.

Operational claims are bounded to 2,000 characters per sentence and 2,000 per document.
Observations are bounded to 4,000 per document; unexamined sections are reported,
not counted as zero. Optional NLP has a 200,000-character document budget,
8,000-character chunks, 4,000 mentions and 1,000 sentences. Cut sentence boundaries
do not become assertions. Source annotations, indexes and discovery bundles have
independent visible caps. The app keeps its 2 GB ingestion-worker address-space
guard and bounds numerical threads inside that worker. Graph materialization
and queries still use memory; million-document serving has not been established.
Identity/dependence comparison blocks are capped at 64 members and 20,000
candidate pairs per algorithm. Passage fingerprints examine at most 100,000
characters per paragraph in 4,000-character windows. These caps and omitted
actor/relationship candidates are disclosed; they are not exhaustive semantic
comparisons. Compressed artifacts have a 512 MiB decoded-size guard.

Fixtures cover all months in both languages, mixed forms, Unicode spans,
ambiguous amounts/dates, modal negation, quotations, qualifiers, contrary claims,
duplicates, ASR withholding, deletion/restoration, clean-rebuild equivalence,
cache/cursor isolation, proof retraction, budgets and corruption. CLI and official
MCP checks use real temporary vaults. HTTP checks use isolated ingestion and
disposable PostgreSQL; their existing test adapter substitutes model ranking,
not ingestion, storage or authentication. These validate implementation contracts,
not general extraction precision or downstream answer accuracy.

A separate integration check uses real local embedding/reranking models,
PostgreSQL and Ladybug. Both English and German questions retrieve the original
approval, German denial and mixed-language conditional statement; reopening the
runtime preserves its cached evidence set. This small fixture verifies the
integration, not ranking quality over a representative corpus.

Local verification on 30 September 2026 after the expanded implementation:

- Broad Python suite: 775 passed, 24 skipped, three failures reproduced on the
  preserved pre-task source (OCR phrase fixture, printable unknown-extension
  classification, and an asynchronous investigation-state assertion).
- Final focused knowledge/parser/publication suite: 121 passed, five skipped.
  Optional EN/DE CNN and bilingual integration suite: 99 passed.
- Real local speech/silence/video checks passed for both English and German.
  Real MCP pagination/validity and local model/PostgreSQL/Ladybug retrieval passed.
- UI: 55 tests passed; typecheck and production build passed. Product: 15 passed.
  Rendered browser QA remains unverified because no in-app browser was available.
- Independent read-only review findings were resolved and their focused
  regressions passed. Source changes are not deployed into the existing service;
  no live collection was rebuilt during these checks.

The initial implementation's local synthetic measurement used 40 text documents, each containing 12
English approvals, 12 German denials and 12 German sentences with amounts
(76,430 source bytes; 960 extracted operational claims). Python 3.13, one
numerical thread, OCR disabled, fresh state, and one unchanged repeat:

| Configuration | First ingest | Unchanged ingest | Artifact bytes | Frozen graph bytes | Peak RSS |
|---|---:|---:|---:|---:|---:|
| Deterministic rules | 0.784 s | 0.491 s | 7,379,788 | 13,987,985 | 108.7 MiB |
| Rules + both optional CNN parsers | 6.877 s | 0.843 s | 15,691,135 | 26,319,899 | 378.9 MiB |

After compression, paragraph caching and unchanged-graph reuse, the same
deterministic fixture (with additional context/dependence metadata) measured
1.320 seconds initially, 0.319 seconds unchanged, 546,086 artifact bytes and
3,478,343 frozen graph bytes; peak RSS was 121.5 MiB. This reduces artifact-plus-
graph storage from about 21.37 MB to 4.02 MB. The paragraph cache added 115,731
blob bytes and 3,000 index bytes across 40 entries. With both optional CNN parsers,
the expanded fixture measured 7.893 seconds initially, 0.525 seconds unchanged,
1,100,955 artifact bytes, 6,148,975 graph bytes, and 417.9 MiB peak RSS; its cache
added 404,324 blob bytes and 3,000 index bytes. Expanded first
ingestion did more work and was slower; compression does not bound graph RAM.

These are single-run measurements on a deliberately dense synthetic fixture,
not a general performance comparison. Artifact and graph byte counts exclude
originals, database files and retrieval indexes. Full provenance and graph
materialization cause substantial storage amplification here; large-corpus
storage and serving efficiency remain limitations.

## Disposition of the original proposals

Every area in the initial discussion was considered. This work selects bounded,
auditable automatic outputs; the suggested libraries were options, not a mandate
to install every algorithm.

| Original area | Current disposition |
|---|---|
| Attributed claims/events/provenance | Sentence-level bilingual rules, payment participant/amount assembly, reported-speaker surfaces and contextual source refs implemented. Arbitrary event/coreference understanding remains outside the grammar. |
| Structural adapters | DOCX revision scopes/headings/references, email quotation scopes, spreadsheet formula/header candidates and PDF run metadata added. Full layout/revision acceptance and arbitrary attribution remain unresolved. |
| Compiler/code graph | Existing mechanical/code discovery remains. No new SCIP or sound call/data-flow resolver was added. |
| Reversible entity resolution | Explicit multi-field comparisons, contradiction checks, qualified support and immutable pair decisions implemented. No destructive/transitive merges or invented matching probabilities. |
| Source dependence | Exact-byte/repeated-claim and bounded near-copy passage groups implemented. Copying direction and independence are not inferred. |
| Time/conflicts | Explicit validity intervals, conservative relative-day anchors, supersession statements and overlapping conflicts added. Arbitrary temporal language and authority resolution remain unsupported. |
| Rule reasoning/validation | Named calendar rule with premises, versioned derivation, source/graph validation and rebuild checks implemented. No general Datalog/ProbLog engine or arbitrary domain transitivity. |
| Dependency-aware refresh | Source/configuration signatures, private shared paragraph caches, source rebinding, unchanged graph reuse and app upgrades implemented. Changed cross-document projections rebuild; no general multi-proof engine. |
| Evidence sets/receipts | MCP/CLI/HTTP, typed hybrid bundles, role/omission accounting and graph readback implemented. Completeness concerns stored records only. |
| Conventional ML/terminology | Optional CNN parsing, EN/DE hints and scoped vocabulary implemented. LSA/trained ranking are not enabled. Graph prediction and rule mining explicitly excluded by the user. |
| Benchmarks | Generated mutations, lifecycle and real protocol/storage checks implemented. Self-produced labels are not presented as independent accuracy evidence. |
| Low-certainty sources | Uncertainty nodes, bounded actual alternate decodes, critical-span diagnostics and independent-reference calibration path implemented. No invented ASR lattice, production accuracy estimate or truth probability. |
