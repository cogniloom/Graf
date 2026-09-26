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
| [Security](SECURITY.md) | Local trust model and vulnerability handling |
| [Third-party notices](THIRD_PARTY.md) | Cosmograph and other dependency licenses |

## License & support

Graf’s original code is [MIT licensed](LICENSE). **The included Cosmograph component is CC BY-NC 4.0 for non-commercial use.** Publishing this repository does not grant commercial rights to Cosmograph. See [third-party notices](THIRD_PARTY.md) and [Cosmograph licensing](https://cosmograph.app/licensing/). This combined distribution is not unrestricted open-source software.

This release targets **a single local owner on Linux**. It is not a public network service or a multi-tenant system. Native macOS, native Windows, WSL2 and unattended production deployments require their own validation.
