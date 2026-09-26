# Docworm

**A local evidence workspace with a connected graph and a Codex research assistant.**

Add documents, follow their relationships in Cosmograph, and ask Codex to investigate with traceable passages. Docworm combines exact identifiers, lexical search, local multilingual embeddings, graph expansion and local reranking. It retains candidate worksets and shows extraction gaps instead of claiming that a short search result is complete.

Graph construction and retrieval do not call a generative model. PostgreSQL runs in Docker Compose; the application and ranking worker run on your computer. The dashboard, including its graph assets, works locally after installation. Codex reasoning uses your configured Codex provider and can transmit the evidence you request.

## Quick start

Requirements: Linux, Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/), and [Docker Engine with Compose](https://docs.docker.com/compose/install/). A source checkout also needs Node.js 22+ and npm to build the dashboard. Model weights and Python dependencies require a substantial initial download; allow at least 15 GB free disk space. CUDA is optional; CPU inference is slower.

From an extracted release archive or checkout:

```sh
./docworm install --demo
./docworm open
```

For an NVIDIA GPU and your own documents:

```sh
./docworm install --device cuda --allow-root "$HOME/Documents"
./docworm sources add "$HOME/Documents/my-case"
./docworm open
```

The installer generates private credentials, starts PostgreSQL, downloads pinned local models, and starts the application. Follow progress under **Activity**. The demo is entirely fictional. No API key is required for indexing or retrieval.

Install the Codex plugin after installing the application:

```sh
./docworm plugin-install
```

Open a new Codex thread and ask: “Use Docworm to investigate whether order 1847 was approved before it was placed. Cite the evidence and explain the conditions.”

## What you can do

- **Sources:** add files or directories; rescan, pause, resume or remove them. Originals remain untouched.
- **Explore:** navigate the Cosmograph visualization, inspect documents and use the document list as an accessible alternative.
- **Activity:** see durable jobs, actual processing phases, failures and readiness.
- **Codex:** search connected evidence, continue candidate worksets and read source passages through local MCP tools.
- **Operations:** start/stop, inspect status, back up the workspace and verify a restore.

A source change gates evidence access until a matching revision is published. `Ready with gaps` means searchable evidence exists but processing exceptions remain. “Ready” describes processing state, not guaranteed understanding or legal completeness.

## Documentation

| Guide | Purpose |
|---|---|
| [Installation](docs/INSTALL.md) | Requirements, CPU/CUDA, paths and first run |
| [User manual](docs/USER-MANUAL.md) | Sources, graph, research and readiness |
| [Codex plugin](docs/CODEX.md) | Installation, tools and privacy boundary |
| [Operations](docs/OPERATIONS.md) | Recovery, backups, upgrades and troubleshooting |
| [Architecture](docs/ARCHITECTURE.md) | Storage, jobs, evidence gates and retrieval |
| [Release guide](docs/RELEASING.md) | Build a clean archive and publish safely |
| [Verification](docs/VERIFICATION.md) | Actual checks and remaining support limits |
| [Security](SECURITY.md) | Local trust model and vulnerability handling |
| [Third-party notices](THIRD_PARTY.md) | Cosmograph and other dependency licenses |

## License and support boundary

Docworm's original code is MIT licensed. **The included Cosmograph component is CC BY-NC 4.0 for non-commercial use.** Publishing this repository does not grant commercial rights to Cosmograph. See [third-party notices](THIRD_PARTY.md) and [Cosmograph licensing](https://cosmograph.app/licensing/). This combined distribution is not unrestricted open-source software.

This release targets a single local owner on Linux. It is not a public network service or a multi-tenant system. Native macOS, native Windows, WSL2 and unattended production deployments require their own validation. Research results require human review.
