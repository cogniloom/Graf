# Installation

Use Linux with Python 3.12 or newer, uv, Docker Engine and Docker Compose v2. Test `docker info`, `docker compose version`, `uv --version`, and `python3 --version` first. Docker must already be accessible to your user; the installer does not change system permissions. A source checkout also requires Node.js 22+ and npm; release archives include built dashboard assets.

Allow at least 15 GB free disk for dependencies and model weights, plus your documents and retained versions. Local multilingual models are substantial: low-memory machines can fail even with a small collection. CUDA testing used an NVIDIA RTX 4060 Ti with 16 GB VRAM. CPU is the portable default; benchmark your own hardware. No automatic switch to a paid inference API occurs.

```sh
./docworm install --demo --device cpu
./docworm open
```

Or select your document directory up front:

```sh
./docworm install --device cuda --allow-root /absolute/documents
./docworm sources add /absolute/documents/case
```

The default private workspace is `$XDG_DATA_HOME/docworm`, falling back to `~/.local/share/docworm`. Use `./docworm --home /absolute/private-workspace install ...` for an isolated workspace. Pass that same global `--home` before later commands, or set `DOCWORM_HOME`. The default inbox/demo is a sibling directory named `<workspace>-sources`, outside the private workspace so its database, credentials and retained content cannot ingest themselves.

The API binds only `127.0.0.1`. Preferred ports are 8765 for the application and 55432 for PostgreSQL; the installer selects unused ports if necessary. Each workspace gets its own Compose project, network, credentials and persistent volume. Ports and paths are recorded in private `app.json` and `compose.json`.

`--models /absolute/model-directory` uses already-downloaded pinned weights. The directory must contain `dense/` and `reranker/`. `--skip-model-download` lets you inspect the interface first; retrieval stays blocked until `./docworm models` finishes and indexing succeeds. Model downloads contact Hugging Face; documents are not sent there. Once installed, inference is offline.

`--runtime /absolute/venv/bin/python` is an advanced verification option for a prepared environment, not the normal installation path. `uv sync --frozen` installs the application's locked dependencies by default.

Run `./docworm allow-root /another/directory` to authorize another source root. This local-owner command restarts a running application. The browser cannot grant itself access outside the allowlist. Source roots cannot overlap the private workspace; symlink sources are rejected.

Native Windows and macOS are not supported by this launcher. WSL2 may provide the required Linux environment but is not part of the verified platform matrix. Do not expose ports through a reverse proxy or bind to a public interface.
