# Verification

This file records the release verification boundary. A build or mocked test is not evidence that real document processing, model execution, browser behavior or recovery works.

## Reproduce

```sh
uv sync --project evidencekg --frozen --extra app
cd product/ui
npm ci
npm test
npm run build
cd ../..
evidencekg/.venv/bin/python -m pytest evidencekg/tests/test_app*.py -q --tb=short
```

Real PostgreSQL lifecycle/API tests are opt-in. Set `EVIDENCEKG_APP_TEST_ADMIN_CONFIG` to an explicitly selected test installation's private administrative JSON; tests create and drop a UUID-named database and never open an existing corpus. The sibling evidencekg.json identifies its constrained runtime role. Use `--tb=short` and never print full connection strings. Without that setting, database-dependent tests skip; a green run containing skips does not certify PostgreSQL integration.

The native model smoke is separately opt-in and requires actual model weights and dependencies. The release acceptance run uses the fictional demo with real local models and a fresh Compose workspace. Browser acceptance must include same-origin authentication, a rendered graph, source changes/removal, publication gating, document inspection, keyboard/mobile interaction and network-error checks.

CI uses a self-hosted runner and never invokes external inference or benchmark grading. It does not run untrusted pull-request code automatically on a private runner. Full model/browser/recovery acceptance remains an explicit local release gate.

## Support boundary

Verified and pending results for the current release are recorded below after acceptance. The original 1,000-document/100-question benchmark is not rerun by product verification. Synthetic mechanics cannot establish legal accuracy or complete understanding of a private collection.

## Acceptance run — 26 September 2026

- Complete core suite: **333 passed, 35 skipped**. Skips were opt-in PostgreSQL/native model groups; they are not counted as passes.
- Application lifecycle/authentication suite independently rerun against real disposable PostgreSQL databases: **25 passed**. These tests use real parsing and verified snapshot import, with model execution substituted.
- Distribution failure regressions: **3 passed**, covering release ancestor symlinks, backup root symlinks and interrupted-install ordering. Independent review reproduced the original failures and rechecked all three fixes.
- Fresh archive installation used its normal `uv sync --frozen` runtime setup, a new Compose project/database, and existing pinned public model weights. Eight fictional documents and three explicit connections reached Ready through actual CUDA preparation. The test did not download weights again.
- Real CUDA query returned five source passages with **zero generative-model calls**. A separate CPU configuration prepared one fictional document and answered a retrieval request through the actual local reranker, also with zero generative-model calls. These are functional checks, not comparative speed or accuracy benchmarks.
- Real source content change immediately gated stale evidence; an unsupported fictional file produced Ready with gaps; removal gated document/graph access and preserved the original directory/files.
- A consistent backup restored successfully into a uniquely named temporary PostgreSQL database; hashes and workspace publication state verified. Full host-loss recovery and relocation to a different absolute home path were not tested.
- Codex marketplace/plugin installation passed in an isolated Codex home. Official MCP SDK initialization, tool discovery, workspace status and document tools passed through the installed launcher. No Codex model inference was invoked.
- Chromium/Orca integration exercised real authentication, document inspection, source removal/re-addition and Cosmograph rendering. Browser graph dependencies are local, with `wasm-unsafe-eval` allowed for WebAssembly and generic JavaScript `unsafe-eval` absent. Supported Arrow/Cosmograph configuration avoids runtime JavaScript generation.
- Independent security review closed all three concrete findings in source/auth/installer/backup/release scope. This is a bounded review, not a security certification.

Linux and the available Chromium browser are the tested environment. Native macOS/Windows, WSL2, Safari/Firefox, multiple untrusted owners, public hosting, unattended reboot integration and complete disaster recovery remain outside the verified scope. No private-corpus accuracy, legal correctness or exhaustive semantic-completeness claim is made.

Final frontend checks: **10 unit tests** plus typecheck/build/format checks passed. The strict-CSP browser suite exercised real Cosmograph with synthetic API fixtures; a separate real-API browser journey verified graph-label selection, readable relationship cards, connected-document navigation and local assets without favicon failures. The lead also inspected desktop/mobile layouts and exercised real source removal/re-addition. Dashboard search is explicitly a document-name/path filter; semantic questions use the Codex tools. A final live shutdown with the browser open completed promptly.

Browser scripts are shipped under `product/ui/tests/`. For reproducible graphics checks, install Chromium and run `uv run --no-project --with playwright python product/ui/tests/browser-smoke.py` from the repository root after building the UI. This script uses synthetic API fixtures. `live-browser.py APP_CONFIG OUTPUT_DIRECTORY` instead exercises a running eight-document fictional demo against the real API and writes screenshots; never use private evidence for public screenshots.

Final standalone Chromium real-API acceptance also passed five viewport changes, three graph remounts, actual document/passages, source listing and Codex instructions with **zero page errors, external requests or failed responses**. The sign-in fragment was removed. One Orca embedded tab crashed after repeated earlier reload/resize operations; standalone Chromium did not reproduce that crash. Embedded-browser stability is therefore not included in the supported-browser claim.
