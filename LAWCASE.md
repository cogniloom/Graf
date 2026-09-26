# Lawcase: exhaustive evidence investigation

This adds a haiku.rag-backed investigation workflow beside the existing
`corpus.py`. Python 3.12+ is required. Direct dependency versions are recorded in
`requirements-lawcase.txt`; the installed environment is used without reinstalling
or changing the active ingestion process. Original source files are not
modified. Use the existing `.venv` from this directory.

```bash
# Already initialized for /home/wenga/Documents/Dossier Scheidung.
.venv/bin/python lawcase.py probe

# After ingestion settles: reconcile originals, copy extracted text, hash units.
.venv/bin/python lawcase.py snapshot

# One separate ChatGPT login for isolated document workers.
.venv/bin/python lawcase.py login
.venv/bin/python lawcase.py doctor

# Inspect the frozen task inventory without model calls.
.venv/bin/python lawcase.py investigate \
  'Identify all agreements, amendments, disputed events and contrary evidence.' \
  --evidence-only --dry-run

# Resume the same run. Omit --max-calls for an uncapped invocation count.
.venv/bin/python lawcase.py resume latest --max-calls 20
.venv/bin/python lawcase.py status
.venv/bin/python lawcase.py verify
.venv/bin/python lawcase.py report
```

All paths are resolved relative to the current directory unless absolute. Use
`--state /absolute/private/state` **before** the subcommand for another case.
Never put state inside the source folder. The registered source root is the
authoritative inventory, including files not successfully indexed by haiku.rag.
An active or changing index can prevent snapshot publication; rerun after it
settles. `probe` does not freeze the corpus or establish successful ingestion.

A custom stdio command is supplied as a JSON argv array, for example
`--mcp-command '["/path/haiku-rag", "--read-only", "mcp", "--stdio"]'`
on `init`.

The investigation makes two exhaustive passes over deterministic source units,
examines pairs of units, builds bounded bundles using original passages, and
preserves references through synthesis and adversarial audit. Similarity is not
proof of a relationship. Allegations, repeated sources, uncertain identities,
amendments and contradictory dates must remain distinguishable.

Pairwise mode is expensive: N units require N(N−1)/2 pair comparisons. For
28,614 units that is **409,366,191 pairs**, in addition to map and synthesis
work. This is deliberate and auditable; elapsed time and subscription allowance
may make such a run impractical. No retrieval ranking silently replaces it.
Start with a small separate test corpus to evaluate reasoning quality and
throughput before committing the full corpus to inference.

Every unit being scheduled or receiving valid JSON is a mechanical property.
It cannot prove that a model noticed every legally meaningful detail. Exact
quotes and reference validation likewise prove provenance, not interpretation.
Failed work, source gaps, unresolved issues and audit findings prevent an
unqualified completion claim. Empty corpora do not count as full coverage.

## Official legal research

Research accepts an **explicit public legal issue**. It does not automatically
send the private investigation question, findings or party names to web search.

```bash
.venv/bin/python lawcase.py research \
  'Switzerland: current statutory and Federal Supreme Court criteria for alternating Obhut, stability of care and parental cooperation. Include contrary and limiting authorities.'

# Use the research.json path printed by that command.
.venv/bin/python lawcase.py investigate \
  'Assess the evidence against the supplied legal criteria, preserving factual disputes.' \
  --authorities .lawcase/authorities/A_EXAMPLE/research.json
```

Only HTTPS URLs on the configured official federal-law/court allowlist can be
fetched. Redirect targets are checked too. Complete fetched bytes and extracted
text are saved and hashed. A proposed quote must occur verbatim, and a separate
worker must support the proposition and section before the authority is admitted.
Failed proposals remain visible in `research.json`. Research pauses propagate
without switching billing; resume from the printed directory with
`.venv/bin/python lawcase.py research --resume .lawcase/authorities/A_EXAMPLE`.
Already verified snapshots are reused after integrity checks.

Verification is limited to the quoted passage and bounded surrounding text.
It does **not** certify legislative currency, exhaustiveness of precedent,
authenticity beyond HTTPS source retrieval, or correctness of model reasoning.
Dynamic Fedlex pages with no usable text fail explicitly; a direct official
text/PDF URL is needed. Cantonal domains are not yet in the default allowlist.
The report must retain the research timestamp and these limitations. A case
without authorities requires the explicit `--evidence-only` option.

## Worker isolation and recovery

Defaults are `gpt-6-astra` with `medium` effort, selected explicitly and recorded.
Choose a different entitled model/effort when creating a run or public research
job. There is no automatic model escalation, API-key fallback, purchase, or
change to account credit settings. Investigation follow-ups default to at most three rounds; new questions left
after that limit remain explicit blockers, never successful completion. Set
`--max-rounds N` when creating the run for a different investigation limit.
`--max-calls` limits new CLI invocations in
one execution, not tokens, money, or internal provider retries. The account's
existing allowance/credit policy still governs usage.

A dedicated `.lawcase/codex-home` contains the ChatGPT login. Evidence workers
run from an empty temporary directory with read-only sandbox, web disabled,
and tool features disabled. Output is rejected if execution events show tools.
Public research permits web search only. The shell environment removes API
key/provider overrides. Model calls still send their supplied evidence to
OpenAI: local retrieval and local SQLite do not make inference local.

State holds sensitive source text, worker prompts/results, reports and login
material. Files created through the CLI use owner-only defaults. Do not publish
or share `.lawcase`. Preserve the whole state directory when resuming; the
snapshot, question, configuration and authority inputs belong to that run.

## Checks

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q lawcase.py lawcase_worker.py \
  lawcase_sources.py lawcase_engine.py lawcase_authorities.py lawcase_report.py
```

Tests use synthetic text and deterministic workers for infrastructure and
failure handling. Such tests are not evidence of legal accuracy or live model
performance. Live connection/model verification is reported separately.

## Source and extraction boundaries

The manifest inventories filesystem files, symlinks and explicit failures. The
current haiku MCP API exposes extracted text and source URIs, but no independently
verified hash binding each indexed extraction to the original file bytes. That
provenance gap stays visible and prevents an unqualified complete assessment.
A frozen text copy does not prove ingestion has stopped or that OCR faithfully
recovered every page. This implementation does not reconfigure or restart your
active parser/OCR process. Empty extracted documents are explicit failures.

Page numbers are not invented: findings use document IDs and exact character
spans in saved extracted text. Embedded attachments, handwriting, diagrams,
missing OCR text and formatting semantics still require parser/source review;
the filesystem inventory alone cannot certify their extraction.

Original passages and contradictory quotations remain attached through
synthesis and audit. When a reduction cannot shrink within its context budget,
the assessment can remain multipart, with each part audited and all evidence
retained. It must not claim a single global synthesis that never occurred.
