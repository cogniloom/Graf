> **Default retrieval:** [Local hybrid retrieval + PostgreSQL](HYBRID.md) is now the CLI/MCP discovery default. Local embeddings/reranking, durable candidate queues and cached results; no generative retrieval calls.

# EvidenceKG

Local Python 3.12 evidence vault, reproducible mechanical graph, official MCP v2
service and durable exhaustive review runner. No embeddings, vector/graph server,
RAG framework or generative model is needed for ingestion or graph construction.
The existing `corpus.py` and `lawcase.py` workflows remain available. Their data
is not migrated, overwritten or silently treated as canonical evidence.

Enhanced CLI review now adds structured semantic observations, independent blind
wording review, joint candidate review, source reconciliation and premise challenges.
See [assurance and paired evaluation](ASSURANCE.md) for the exact workflow,
commands, comparison design and limitations. This reduces missed-evidence risks;
it does not provide a zero-miss guarantee.

See [ranked discovery and benchmarking](RANKING.md) for the optimized discovery endpoint and optional attributed bilingual index.
See [accuracy-first discovery](ACCURACY.md) for explicit subscription-assisted question planning and original-passage review.

## Install and start

Linux, local filesystem, Python 3.12, SQLite with FTS5, and `uv`:

```bash
cd evidencekg
uv sync --locked --python 3.12
uv run evidencekg --state /local/case-state init /path/to/documents
uv run evidencekg --state /local/case-state ingest
uv run evidencekg --state /local/case-state inventory --limit 100
uv run evidencekg --state /local/case-state search 'not' --mode literal
uv run evidencekg --state /local/case-state verify
uv run evidencekg --state /local/case-state serve --transport stdio
```

`--state` is a global argument, before the subcommand. Its directory must be
outside the source corpus. It contains private originals, extracted text, model
inputs/results and reports. Use local storage, not NFS/SMB/network mounts. WAL,
foreign keys, FULL synchronization and a process writer lock are enabled; FTS5
availability is checked on opening. Network serving is deliberately not exposed
in V1. Wider transports require a separately implemented authentication boundary.

`uv.lock` freezes the tested dependency graph, including official `mcp==2.2.0`.
The package requires `mcp>=2,<3` and uses `MCPServer`, never v1 FastMCP.

For actual OCR install Tesseract, Poppler (`pdftoppm`) and the selected language
packs. Missing tools/data produce explicit extraction gaps. A trusted JSON
configuration can specify `tessdata` when language files are outside the system
location. No language data is fetched automatically by ingestion.

```json
{
  "ocr_languages": "deu+eng",
  "tessdata": "/local/tessdata",
  "segment_chars": 6000,
  "identifiers": [
    {"namespace": "authority-X", "pattern": "Case: (?P<value>[0-9]+)"}
  ],
  "names": ["Alex Smith"],
  "terms": ["written consent"],
  "excludes": ["*.tmp"]
}
```

```bash
uv run evidencekg --state /local/case-state init /path/to/documents --config rules.json
# Change future extraction/rules; retained snapshots and runs remain immutable.
uv run evidencekg --state /local/case-state configure revised-settings.json
uv run evidencekg --state /local/case-state ingest
# Explicitly retry partial extraction after fixing OCR or extraction settings.
uv run evidencekg --state /local/case-state ingest --reextract
```

Failed parser attempts are retried as new immutable extractions. Ready/partial
unchanged extractions are reused unless configuration/parser versions change or
`--reextract` is requested. Changing the corpus root requires another state.
Identifier patterns are trusted administrator configuration, not source-supplied
instructions. They must be bounded grammars suitable for the corpus.

## Captured scope and formats

Every enumerated file remains visible as ready, partial, failed, unsupported or
excluded. Empty captured files are explicitly marked. Files are opened using
pinned directory descriptors without following symlinks; changed inode/size/
mtime/ctime observations reject unstable acquisition. A symlink, inaccessible
directory or unenumerated container depth makes the overall denominator unknown.
Known records are still enumerated; `inventory.total` is then null and
`known_matches` reports the records actually known.

Supported and fixture-tested:

- UTF-8/BOM/UTF-16 TXT and MD, preserving whitespace and qualifications. Invalid
  encoding uses a flagged replacement representation; original bytes remain.
- PDF via pypdf: native page text and annotations, embedded attachments, OCR on
  text-absent pages. Rendered OCR pages are retained. Text-bearing pages can still
  contain unreviewed handwriting/images; all PDF extraction carries a visual gap.
- DOCX via python-docx/lxml: paragraphs in document XML parts, including table
  text, notes/comments and text-bearing revisions; raw XML and table arrays are
  retained. Formatting/revision semantics and visuals remain unreviewed. Embedded
  objects are inventoried as children and may be unsupported.
- EML via standard-library `email`: raw header values, body alternatives,
  hierarchical MIME paths, nested messages and attachments. Decoded children
  are derived representations; the exact original message wire bytes remain in
  their parent. Limits retain known child metadata and disclose unknown descendants.
- PNG/JPEG/TIFF/BMP/WebP via Pillow and local Tesseract. Frames are rendered and
  retained separately. OCR output is fallible and never proves visual review.

Other formats, including spreadsheets/slides/archives, remain unsupported until
fixture-backed adapters are added. They are not silently dropped. Binary parser
subprocesses have memory/CPU/file/time limits and a Linux seccomp network denial
inherited by OCR children. Failure to establish that policy fails extraction.
This is not a complete filesystem sandbox for hostile parser exploits. Never run
this local single-user service as a public upload endpoint.

Canonical text and raw artifacts are separate from search normalization. Primary
segment ranges partition the complete canonical text, including empty segments
for empty/gap records. Target size is 6,000 characters (roughly 1,000–2,000 tokens
for ordinary prose); the configured input-byte budget can reduce it. Up to 300
adjacent characters are context only. Character counts are not provider token
counts; adapters must use compatible input limits and must never truncate.
Locators identify real pages, MIME/header parts, XML paragraphs or image frames;
DOCX/TXT/EML receive no invented page numbers.

Citation offsets are Python Unicode code-point indices into immutable segment
text, with an inclusive start and exclusive end; they are not UTF-8 byte offsets.
Acquisition checks each file for instability but is not an atomic snapshot of a
changing filesystem. Quiesce the source folder when a single-instant capture is
required.

## Mechanical graph and retrieval

Features preserve raw spans and namespaced canonical keys. Supported rules:
byte identity (checksum and byte verification), attachment ancestry, exact
Message-ID reply observations, configured identifiers, `[[exact/relative/path]]`
references, observed source-version sequence, exact normalized paragraphs,
email/URL surfaces, configured names/terms and absolute dates. Numeric dates keep
all valid day/month alternatives; relative dates are not anchored automatically.
MIME identity uses parent source identity and MIME part, separately from physical
paths, so display-path collisions cannot invent source ancestry.

Paragraph matching collapses whitespace only, retaining case, punctuation,
accents and negation. Matching is paragraph-based, not shingle, paraphrase or
fuzzy matching. All paragraphs, singleton features and common keys are indexed.
Long paragraph descriptors have explicit previews, hashes, lengths and original
span access. Hash hits are verified against original extraction sequences.
Feature memberships grow linearly. No shared-key document clique is stored.
`neighbours` enumerates memberships, not a probability of relationship.
`related` ranks explicit links first, distinctive keys next, weak surfaces last;
its IDF rank is only a discovery ordering and exposes evidence-tool continuations.

Email/path references resolve after the entire snapshot is populated; missing
references remain unresolved and duplicate targets remain ambiguous. Source
version observations mean changed acquired bytes, never legal supersession.
Six copies of an allegation remain six source entries in one identity group,
with no corroboration score. Interpretations cannot become mechanical edges.

Two search modes:

- `lexical`: an escaped Unicode FTS5 **phrase**, using `unicode61` with case and
  diacritic folding and BM25. Arbitrary FTS operators/SQL are not accepted. Each
  immutable snapshot has its own FTS index so future documents cannot change
  old BM25 ordering or invalidate cursors. This adds index storage per snapshot.
- `literal`: verify exact strings in complete immutable canonical text, including
  strings crossing segment boundaries. Absence concerns extracted text only,
  never unreadable originals or unseen visuals. Short strings are supported.

All application cursors bind snapshot, operation and query, with stable keyset
ordering; page sizes may change. `remaining`, `next_cursor`, or remaining segment
IDs expose bounded continuations. Read budgets reject an individually oversized
item explicitly. No source text is silently truncated. Some ranked/derived
queries currently materialize their candidate set before paging; benchmark
results are not a million-document performance promise.

## MCP tools

Configure your MCP client with the absolute installed `evidencekg` executable and
arguments `--state /local/case-state serve --transport stdio`. Stdout is protocol
only. SDK-client tests exercise actual subprocess transport and structured output.

Read tools: `inventory`, `document_details` (all warnings/artifacts), `sections`,
`read_segments`, `read_original_region`, `search`, `neighbours`, `related`, and
`explain_connection`. `read_original_region` serves bounded base64 bytes of the
captured original or a named preserved render/artifact; it never accepts a
filesystem path. Use `document_details(kind="artifacts")` for artifact names.
Native PDF page-specific renders are not synthesized by this read-only tool;
read the preserved PDF bytes or existing OCR render. Bounding-box crops and
vision-model consumption are not implemented; visual work stays explicitly open.

Queue/write tools: `start_review`, `next_review_task`, `submit_review_result`,
`review_status`, `store_interpretation`. No arbitrary SQL, shell command, model
configuration or filesystem access is exposed through MCP. A trusted caller may
append attributed unreviewed proposals with exact citations, but cannot alter
originals, silently merge identities or promote them into observations.

## Exhaustive review and workers

Scheduling alone makes no provider calls:

```bash
uv run evidencekg --state /local/case-state review \
  --question-file question.txt --exhaustive --schedule-only
uv run evidencekg --state /local/case-state status RUN_ID
```

For this repository's existing hardened, subscription-only Codex adapter:

```bash
uv run evidencekg --state /local/case-state resume RUN_ID \
  --lawcase-project /absolute/path/to/Graf \
  --lawcase-worker-state /absolute/path/to/Graf/.lawcase \
  --model gpt-6-astra --effort medium --max-calls 20
```

The selected existing dedicated worker state must already have the supported
ChatGPT login/configuration. Credentials are not copied or created by this
package. There is no API-key fallback, model fallback, credit purchase or paid
overage setting. Source passages go to the configured model service; local
storage does not imply local inference. The adapter disables tools and rejects
unexpected tool activity using the existing lawyer worker implementation.

Alternatively, `--worker-command '["/absolute/executable", "arg"]'` selects an
administrator-controlled executable reading one JSON task envelope on stdin and
returning one schema-constrained JSON result on stdout. This is a trusted adapter
boundary; the executable must enforce its own model/tool isolation and billing
policy. The MCP caller and source documents cannot set that command. The model,
effort, adapter and prompt/schema identity are frozen for a run.

Pass A schedules every primary segment plus separate extraction/modality/inventory
gap tasks. Pass B presents original endpoint passages in anchored feature
membership packets and explicit-link packets, including frozen historic version
endpoints. Covering memberships does not cover every pair in a hub. Pass C
persists new issues, builds deterministic lexical/graph candidate bundles and,
in exhaustive mode, reconsiders every primary source unit against each newly
scheduled issue. Investigation rounds are finite; bounded candidate discovery
is explicitly a preview, and issues remain open unless separately resolved.
Pass D exports the complete ledger and an issue-by-issue report with quoted
premises, uncertainty and attributed support/opposition classifications.

Queue IDs, leases, input artifacts, prompt/schema hashes, model identity, failed
attempts and accepted results survive interruption. Exact task IDs, lease IDs,
input hashes, extraction/segment IDs and character spans are validated. Empty
findings are allowed. Malformed or invented citations do not complete a task.
Retries are finite; terminal resumes make no additional worker calls. A quota or
call cap pauses safely, and failed/unresolved/round-limit states remain explicit.
For changed prompt/schema contracts or exhausted attempts, start a new run on the
retained snapshot; do not silently reinterpret an archived run.

## Reports, integrity and backup

```bash
uv run evidencekg --state /local/case-state export RUN_ID --output ./report
uv run evidencekg --state /local/case-state verify
uv run evidencekg --state /local/case-state backup /local/backups/case-001
uv run evidencekg restore /local/backups/case-001 /local/restored-case
```

Persistent human-readable/JSON status starts with ingestion; review status files
refresh during execution (at most once per second) and on runner settlement.
SQLite remains authoritative between updates. Export includes `report.md`,
`coverage.json`, `inventory.csv`, frozen `manifest.json`, complete `findings.json`,
`issues.json`, `issue_report.json` and `interpretations.json`. Quotations are
revalidated during export. Historic proposals report when their cited extraction
is stale relative to the current snapshot without rewriting historic findings.

`verify` checks SQLite/foreign keys, all artifact checksums, primary partitions,
FTS contents, deterministic feature/link reproduction, complete snapshot graph
membership, scheduled source/graph coverage, citations and audit hashes. Backups
use SQLite's backup API under the writer lock, copy artifacts and verify the
restored store. Restore requires a new destination outside the corpus. Keep
backup checkpoints outside the working directory for stronger tamper evidence.
An administrator able to rewrite both evidence and logs can rewrite a hash chain;
it is not independent proof of authenticity.

Measured coverage is **all scheduled inputs have validated results under a frozen
scope**. It is not "100% understood", complete semantic discovery, perfect OCR,
examination of every combination, independent corroboration, or legal truth.

## Verification and evidence

```bash
uv run pytest -q
# If English OCR data is outside the system installation:
EVIDENCEKG_TEST_TESSDATA=/local/tessdata uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/benchmark.py --count 20000 --output examples/benchmark-20000.json
```

The release evidence and precise limitations are recorded in `RELEASE.md`.
Fixtures are synthetic; live model quality is separate from software assertions.
The tests include 1,201-file scheduling/delivery, SDK enumeration beyond 1,000,
negation/singletons, IDs/names/dates, late/ambiguous email targets, MIME ancestry
and limits, copy groups, native/OCR extraction, mutation/symlink rejection,
lease/idempotency/retry failures, joint amendments, integrity and restore.

## Primary references

- [SQLite FTS5](https://www.sqlite.org/fts5.html) and [WAL](https://www.sqlite.org/wal.html)
- [Official MCP Python SDK v2](https://github.com/modelcontextprotocol/python-sdk)
- [MCP protocol pagination](https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination)
- [Python email/MIME](https://docs.python.org/3/library/email.html)

Docling or haiku.rag can be added behind the parser boundary only if the adapter
provides complete versioned content, provenance locators and gaps. Search results
or short summaries are not sufficient imports. Embeddings, shingles, fuzzy entity
merging, graph servers, distributed workers and legal truth automation are deferred.
