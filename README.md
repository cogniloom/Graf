<p align="center">
  <img src="docs/assets/graf-logo.png" alt="Graf — a modern knowledge graph, focused on accuracy rather than speed." width="640">
</p>

<p align="center">
  <a href="https://github.com/cogniloom/Graf/actions/workflows/release.yml"><img src="https://github.com/cogniloom/Graf/actions/workflows/release.yml/badge.svg?branch=main" alt="Build and release workflow status on main"></a>
  <a href="https://github.com/cogniloom/Graf/releases"><img src="https://img.shields.io/github/v/release/cogniloom/Graf?include_prereleases&amp;label=release&amp;color=8b5cf6" alt="Latest release, including release candidates"></a>
  <a href="docs/INSTALL.md"><img src="https://img.shields.io/badge/platform-Linux-38bdf8?logo=linux&amp;logoColor=white" alt="Platform: Linux"></a>
  <a href="evidencekg/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.12 or newer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/original_code-MIT-22c55e" alt="Original Graf code: MIT license"></a>
  <a href="THIRD_PARTY.md"><img src="https://img.shields.io/badge/Cosmograph-CC_BY--NC_4.0-f59e0b" alt="Cosmograph: CC BY-NC 4.0, non-commercial use"></a>
</p>

<p align="center">
  <strong>Your documents. Their connections. Evidence you can trace.</strong><br>
  A local evidence workspace with an interactive graph and a Codex research assistant.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="docs/USER-MANUAL.md">User manual</a> &nbsp;·&nbsp;
  <a href="docs/CODEX.md">Codex plugin</a> &nbsp;·&nbsp;
  <a href="https://github.com/cogniloom/Graf/releases">Releases</a>
</p>

---

## Follow the evidence

Add documents, explore their relationships in Cosmograph, and ask Codex to investigate with traceable source passages. Graf combines exact identifiers, lexical search, local multilingual embeddings, graph expansion and local reranking to help you find connected evidence.

Candidate worksets stay available for continued review. Extraction gaps stay visible. A short search result is never presented as proof that you have seen everything.

| Bring your sources | Explore the connections | Investigate with context |
| :--- | :--- | :--- |
| Add files or directories. Rescan, pause or resume processing while originals remain untouched. | Navigate the interactive graph, inspect documents, or use the accessible document list. | Ask Codex to search connected evidence, continue candidate worksets and read source passages through local MCP tools. |

| Stay informed | Keep control |
| :--- | :--- |
| Follow durable jobs, processing phases, failures and readiness under **Activity**. | Start and stop the application, inspect status, back up your workspace and verify a restore. |

### Local processing, an explicit research boundary

Graph construction and retrieval do not call a generative model. PostgreSQL runs in Docker Compose; the application and ranking worker run on your computer. The dashboard and its graph assets work locally after installation. **No API key is required for indexing or retrieval.**

> [!IMPORTANT]
> Codex reasoning uses your configured Codex provider and can transmit the evidence you request. See the [Codex guide](docs/CODEX.md) for the privacy boundary.

## Graf benchmarks

**Less searching. More evidence in the first model response.** Graf retrieves relevant passages before GPT-6-astra answers, reducing the model turns needed to find and read evidence. We measured that workflow against the same model using file search and reading without a knowledge graph.

| Document token reduction | Document median time reduction | Code answers passing automated review |
|:---:|:---:|:---:|
| **71.6% fewer** | **54.4% lower** | **8/9 with Graf · 5/9 baseline** |
| 1,000 fictional operational records | 30.53 s → 13.91 s | 189 real source/doc files |

*Measured pilot · 26 September 2026 · one repetition · same-model automated grading · two paired rubric exclusions.* These figures describe the tested workloads. Code latency is sensitive to the exclusions; it does not establish a general code-speed advantage.

[![Graf benchmark: model tokens, elapsed time and automated answer quality for documents and source code](benchmarks/results/2026-09-26/comparison.png)](benchmarks/results/2026-09-26/REPORT.md)

[**Full report**](benchmarks/results/2026-09-26/REPORT.md) · [**All 52 original trials**](benchmarks/results/2026-09-26/per-case.csv) · [**Methodology**](benchmarks/METHODOLOGY.md) · [**Run it yourself**](benchmarks/README.md)

### Same model, measured end to end

Both arms requested **GPT-6-astra, medium effort**, with a six-response limit. The baseline had controlled literal file search and bounded reading. Graf had those same tools plus initial context from the actual hybrid engine: lexical retrieval, local embeddings, graph/structural context and reranking. This tests the whole retrieval stack, not graph edges in isolation or an unrestricted native Codex session.

| Workload | Approach | Mean model tokens ↓ | Mean uncached input ↓ | Median elapsed ↓ | Strict automated passes ↑ |
|---|---|---:|---:|---:|---:|
| **1,000 documents** | File-search baseline | 57,567 | 31,851 | 30.53 s | 15/15 |
| 15 valid question pairs | **Graf + file tools** | **16,340** | **9,386** | **13.91 s** | **15/15** |
| **189 source/doc files** | File-search baseline | 80,823 | 43,704 | 44.99 s | 5/9 |
| 9 valid question pairs | **Graf + file tools** | **64,613** | **41,846** | **41.17 s** | **8/9** |

Model tokens include cached input and output, measured from actual subscription receipts. They are not dollar-cost estimates. Elapsed time includes retrieval, tool execution, process startup and model/network wait. A strict pass requires correctness, completeness, source support, valid verbatim citations and appropriate abstention. Human adjudication is pending.

The document corpus contains **9,946 text lines** of fictional operational records: amendments, invoices, incident reviews, conflicting policies, multilingual correspondence and missing facts. The real code corpus contains **31,039 physical text lines** across source, configuration and documentation, with questions about cross-module behavior, retries, parser failures and snapshot isolation. These are not executable-line counts or million-line repository results.

### Retrieval across 10,000 documents

| Actual hybrid engine | Measured result |
|---|---:|
| Corpus size | **10,000 short documents · 99,946 lines** |
| Complete designated evidence sets retrieved | **16/16** |
| Median retrieval | **1.97 s** |
| p95 retrieval, including first-query model loading | **18.34 s** |
| Result-cache hits | **0** |
| Generative-model calls during retrieval | **0** |

This separate scale probe measures retrieval coverage, **not final-answer accuracy at 10,000 documents**. Increasing the corpus adds distractors while retaining the same questions. [Inspect the scale results →](benchmarks/results/2026-09-26/hybrid-10000-scale.json)

<details>
<summary><strong>Setup costs, grading usage and comparisons where both answers passed</strong></summary>

Indexing and preparation are additional to answer latency. Local model weights were already present; no model download was timed. Initial software installation and database provisioning are excluded.

| Corpus | Ingestion | Hybrid preparation | Generative calls for indexing/preparation |
|---|---:|---:|---:|
| 1,000 documents | 5.95 s | 18.60 s | 0 |
| 189 source/doc files | 1.63 s | 26.34 s | 0 |
| 10,000 documents | 72.56 s | 72.18 s | 0 |

The separate automated judge consumed the following resources, excluded from answering metrics:

| Workload | Judge calls | Input tokens | Output tokens | Summed elapsed |
|---|---:|---:|---:|---:|
| Documents | 32 | 459,361 | 2,680 | 251.9 s |
| Code | 20 | 441,242 | 1,876 | 171.6 s |

For paired questions where **both approaches passed**, Graf saved:

| Workload | Both-pass pairs | Mean tokens saved per pair | Mean seconds saved per pair |
|---|---:|---:|---:|
| Documents | 15 | 41,227 | 17.21 s |
| Code | 5 | 39,672 | 1.56 s |

These subsets explain efficiency at matched observed quality. They do not replace the full accuracy denominators above; unsuccessful answers remain in overall usage and scoring.

</details>

<details>
<summary><strong>All original results, rubric corrections and latency sensitivity</strong></summary>

All **52 answering trials and 52 separate grades completed**. No original results were deleted. Grading exposed two authored-reference defects: `doc-16` incorrectly required abstention for an answerable policy question; `code-03` overstated process-group reaping. Both arms of each question were excluded from the headline comparison, and the corpus builder was corrected for future runs.

| Original workload | Approach | Mean model tokens | Median elapsed | p95 elapsed | Original strict score* |
|---|---|---:|---:|---:|---:|
| 16 document questions | File-search baseline | 56,608 | 30.53 s | 44.97 s | 15/16 |
| 16 document questions | Graf + file tools | 16,351 | 14.09 s | 28.95 s | 15/16 |
| 10 code questions | File-search baseline | 78,949 | 43.85 s | 56.60 s | 5/10 |
| 10 code questions | Graf + file tools | 63,653 | 45.73 s | 97.62 s | 9/10 |

*These scores include defective gold and are retained for audit, not headline accuracy claims.*

**The code median changes direction after the exclusion.** Across all original questions, Graf took longer despite using fewer tokens. Across the valid pairs, its median was lower. A broader repeated experiment is needed before claiming a code-speed advantage.

[Document summaries](benchmarks/results/2026-09-26/documents-summary.json) · [Code summaries](benchmarks/results/2026-09-26/code-summary.json) · [Document paired analysis](benchmarks/results/2026-09-26/documents-quality.json) · [Code paired analysis](benchmarks/results/2026-09-26/code-quality.json)

The paired-analysis JSON retains the original denominators, category results and descriptive question-bootstrap intervals, including the excluded items. The [primary document metrics](benchmarks/results/2026-09-26/documents-primary.json) and [primary code metrics](benchmarks/results/2026-09-26/code-primary.json) apply the stated exclusions.

</details>

<details>
<summary><strong>Earlier retrieval probes and interrupted calibration</strong></summary>

These local probes compare lexical and mechanical compatibility retrieval policies, **not the full hybrid engine or GPT answer accuracy**. Each question was repeated three times with a twelve-passage limit; operating-system caches were not flushed.

| Corpus | Policy | Complete reference sets | Designated-reference recall | Median retrieval | p95 retrieval |
|---|---|---:|---:|---:|---:|
| 1,000 documents | Lexical | 48/48 | 100% | 0.235 s | 0.677 s |
| 1,000 documents | Mechanical | 48/48 | 100% | 0.405 s | 1.106 s |
| 189 source/doc files | Lexical | 9/30 | 27.27% | 0.097 s | 0.201 s |
| 189 source/doc files | Mechanical | 9/30 | 31.82% | 0.344 s | 0.536 s |

An earlier model calibration stopped after 28 completed trials because the model never invoked the optional Graf tool. It cannot establish a Graf treatment effect and is excluded. The measured hybrid protocol supplies timed retrieval before the first response.

An older mechanical/lexical 10,000-document probe stopped after 861.6 seconds without complete aggregates. That interruption provides no per-query latency estimate and is not a result for the current hybrid engine. Its receipts, the calibration and earlier probes remain in the local raw evidence directory.

</details>

### Evidence and limits

Measurements used a shared Linux host with an **AMD Ryzen 9 5950X and NVIDIA RTX 4060 Ti 16 GB**, pinned local models and an isolated PostgreSQL database. The database was removed after measurement. The code corpus captures revision `00be47ad82e0fd126f571062dafd979b60e3a9bf`; source files were exported losslessly to supported text formats, without adding AST or call-graph analysis.

This is a small, authored pilot with one repetition, fictional short documents and same-model automated judging. It does not establish real-customer PDF/OCR accuracy, million-line code performance, or universal speed/cost savings. The CLI records the requested model but does not independently attest the server's effective model identity. Human review and fresh held-out, repeated runs remain necessary for broader marketing claims.

**Verification:** 49 benchmark tests passed. Four existing OCR/MCP failures reproduced on the unchanged base and remain documented. [Verification details](benchmarks/results/VERIFICATION.md) · [Environment](benchmarks/results/2026-09-26/environment.json) · [Reproduction and raw-artifact requirements](benchmarks/results/README.md)

## Quick start

### 1. Prepare your machine

| Requirement | Details |
| :--- | :--- |
| Platform | Linux with Python 3.12+ |
| Tools | [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Docker Engine with Compose](https://docs.docker.com/compose/install/) |
| Disk space | At least **15 GB free** for dependencies and model weights, plus space for documents and retained versions |
| Source checkouts | Node.js 22+ and npm to build the dashboard; release archives include it prebuilt |
| GPU | Optional NVIDIA CUDA support; CPU inference is slower |

Download `graf-X.Y.Z.tar.gz` and its checksum from [GitHub Releases](https://github.com/cogniloom/Graf/releases), then follow the [verification and extraction steps](docs/RELEASING.md). Dependencies and model weights require a substantial initial download.

### 2. Open your first workspace

From an extracted release archive or source checkout:

```sh
./graf install --demo
./graf open
```

The installer generates private credentials, starts PostgreSQL, downloads pinned local models and starts the application. Follow progress under **Activity**. The demo documents are entirely fictional.

<details>
<summary><strong>Use your own documents with an NVIDIA GPU</strong></summary>

<br>

```sh
./graf install --device cuda --allow-root "$HOME/Documents"
./graf sources add "$HOME/Documents/my-case"
./graf open
```

Replace `my-case` with your document directory. Use `--device cpu` for CPU inference. See [Installation](docs/INSTALL.md) for model locations, source permissions and existing workspaces.

</details>

### 3. Connect Codex

Install the plugin after installing the application:

```sh
./graf plugin-install
```

Open a new Codex thread and try this question against the demo:

> Use Graf to investigate whether order 1847 was approved before it was placed. Cite the evidence and explain the conditions.

## Know what “ready” means

A source change gates evidence access until a matching revision is published. **Ready with gaps** means searchable evidence exists, but processing exceptions remain. **Ready** describes processing state, not guaranteed understanding or legal completeness. Research results require human review.

## Go deeper

| Guide | What you’ll find |
| :--- | :--- |
| [Installation](docs/INSTALL.md) | Requirements, CPU/CUDA, paths and first run |
| [User manual](docs/USER-MANUAL.md) | Sources, graph, research and readiness |
| [Codex plugin](docs/CODEX.md) | Installation, tools and privacy boundary |
| [Operations](docs/OPERATIONS.md) | Recovery, backups, upgrades and troubleshooting |
| [Architecture](docs/ARCHITECTURE.md) | Storage, jobs, evidence gates and retrieval |
| [Release guide](docs/RELEASING.md) | Build a clean archive and publish safely |
| [Verification](docs/VERIFICATION.md) | Actual checks and remaining support limits |
| [Benchmarks](benchmarks/README.md) | Reproducible document/code comparisons, measured tokens, latency and supported-answer scoring |
| [Security](SECURITY.md) | Local trust model and vulnerability handling |
| [Third-party notices](THIRD_PARTY.md) | Cosmograph and other dependency licenses |

## License & support

Graf’s original code is [MIT licensed](LICENSE). **The included Cosmograph component is CC BY-NC 4.0 for non-commercial use.** Publishing this repository does not grant commercial rights to Cosmograph. See [third-party notices](THIRD_PARTY.md) and [Cosmograph licensing](https://cosmograph.app/licensing/). This combined distribution is not unrestricted open-source software.

This release targets **a single local owner on Linux**. It is not a public network service or a multi-tenant system. Native macOS, native Windows, WSL2 and unattended production deployments require their own validation.
