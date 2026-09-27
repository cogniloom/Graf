# Investigation contract

Graf owns investigation sessions, runs, artifacts and provenance. Agent adapters are replaceable. A run pins a source snapshot and records whether it started against an incomplete collection. Follow-ups create new linked runs; they never rewrite completed results. Browser lifetime is independent of execution.

## Record boundaries

The artifact vault contains exact source bytes, extraction representations, prompts, provider events, final answers, generated documents and annotations. Artifacts have opaque identities, immutable hashes and typed dependencies. Generated claims are not original evidence. Exposed reasoning summaries are recorded; private internal reasoning is neither requested nor claimed.

The integrity ledger contains minimal attributed action records and commitments; potentially erasable content stays in artifact payloads. Signed checkpoints bind artifact and relationship inventories and log continuity. An offline verifier distinguishes integrity, self-consistent signature and externally pinned identity. No local timestamp or self-contained key proves independent time or identity.

## Coverage

Snapshot membership, retrieval, supplied text, validated citation and human review are separate facts. Supplying text does not prove comprehension. Citation validation checks source binding and exact quotations; it does not establish truth or semantic support. Unobserved access is reported, never silently treated as complete coverage.

## Lifecycle

Corrections supersede; withdrawals restrict use; erasure removes payloads and dependencies under an attributed instruction. Erasure is an explicit preview/confirm operation and produces durable intent, execution and failure records. Concurrent readers/runs and ingestion must be fenced. Retained tombstones are minimal; external copies/backups remain explicit obligations. Byte overwrite or deletion on an SSD is not a guarantee of forensic destruction. Historical checkpoints remain historical commitments even after authorized erasure makes their payloads unavailable.

## Product surface

Sessions (default), Sources, Graph and Settings. A session exposes results, activity, evidence coverage, outputs, graph and a versioned package download. Source and output previews are served as text or attachments, never executable agent HTML. All endpoints retain authenticated loopback and same-origin protections.

## Delivery gates

1. Vault, signed checkpoints, export/verifier and tamper/deletion tests.
2. Durable Codex execution, authenticated endpoints, source snapshot capture and verified output packages.
3. Research UI, snapshot warnings, follow-ups, provenance graph and output/citation inspection.
4. Dependency erasure, source exclusions, recovery/backup verification and independent review.

Legal review must define jurisdiction-specific preservation, disclosure and identity requirements before making court-suitability claims.

## Local use

Start Graf with its normal launcher, open the authenticated loopback URL, and use
**Sources** to register individual files or directories. **Sessions** is the default
page. Indexing status describes the current source revision; starting early requires
explicit consent and an available, internally consistent extraction snapshot. A run
retains the exact snapshot it started with, even if files subsequently change.

The prompt starts a durable run with a stable ID. Closing the browser does not cancel
it. Results, observable provider activity, exact-quote checks, source coverage and
generated documents are retained as separate artifacts. Follow-ups create linked
runs. History supports comparison and attributed withdrawal; output revisions create
new artifacts with a supersedes relationship. Generated HTML is downloaded or shown
as literal text, never executed in the dashboard.

The graph includes documents, passages, extracted features/occurrences, explicit
relationships and investigation provenance. The session graph distinguishes supplied
or cited records from gray background records. Highlights establish recorded exposure,
not model comprehension or unobserved graph traversal. Large views are bounded and
show their counts and truncation state.

## Verification and trust

Download a package from the selected run. It includes retained source bytes, snapshot
and prompt context, captured provider events, results and generated documents, a
readable report, commitments and signatures. Run the independent verifier:

```sh
python -m evidencekg.investigations.verifier evidence.zip
python -m evidencekg.investigations.verifier evidence.zip --trusted-key PUBLIC_KEY_HEX
```

Obtain the public key through an independent trusted channel. Download and preserve
signed checkpoints outside the Graf installation. A package carrying its own public
key can prove internal consistency, but cannot by itself establish the signer's
identity or detect replacement of the entire installation and its key. A retained
external checkpoint is necessary to detect rollback relative to that checkpoint.
Local clock timestamps are observations, not trusted timestamp-authority attestations.

The Codex adapter uses existing subscription authentication. No automatic provider
retry or API-key billing fallback is part of the execution contract. Observable
reasoning summaries can be retained; inaccessible private model reasoning cannot.
Requested model identity and provider-attested identity are distinct. Exact quotation
checks do not judge the truth or semantic relevance of an answer.

## Attributed erasure

In Evidence, select artifacts, enter the instruction or reason, review the computed
impact, and type the exact confirmation. The preview includes dependent outputs,
other retained copies and affected follow-ups. Active runs must stop before a fresh
preview can authorize deletion. Graf persists intent and source exclusions first,
then erases managed payloads and records a receipt. On restart, it retries previously
authorized incomplete operations using their stored scope. Failed operations retain
restrictions; failure never restores withdrawn evidence.

Erasure through the UI does not remove original user files, older indexing
generations, PostgreSQL projections or independently exported copies. For legacy
managed generations/projections, stop Graf and use a separately privileged database
configuration. The first command previews only:

```sh
python -m evidencekg.investigations.erasure_admin \
  --config /absolute/app.json --admin-config /absolute/admin.json --action ACTION_ID
```

Review the entire affected-generation inventory: cleanup removes whole affected
generations, which can contain unrelated bytes. Repeat the command with
`--confirm EXACT_PREVIEW_DIGEST` only after reviewing that scope. The runtime database
role cannot issue these deletions or create its own authorization. The administrative
operation leaves attributed commitments and a receipt. Logical filesystem and SQL
deletion do not promise forensic destruction on storage media.

Backups include investigation history, deletion restrictions and signing keys, and
therefore require the same protection as evidence and credentials. Restoring a backup
from before an erasure can resurrect evidence: reconcile the newest deletion records
and external checkpoints before making a restored installation available. Copies
outside Graf remain an explicit custodian obligation.

## Current limits

Investigations capture at most 256 MiB of original source bytes per run and reject
larger collections explicitly. The initial provider context is bounded; unsupplied
passages remain recorded as unsupplied, not assessed as irrelevant. Graph views are
bounded to 5,000 nodes and 20,000 relationships. The installation identifies the
local authenticated owner; it is not a multi-user identity or independent witnessing
system. Jurisdiction-specific legal acceptance requires a separately validated
procedure, identity policy, preservation policy and expert review.
