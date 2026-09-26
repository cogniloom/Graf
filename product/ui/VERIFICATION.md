# Docworm dashboard handoff

Scope: `product/ui` only. React 18 + TypeScript + Vite, real `@cosmograph/cosmograph` 2.5.1 under its CC-BY-NC-4.0 license. No custom canvas substitute, CDN runtime loading, telemetry, demo records, or document-provider calls exist in application code. Synthetic data exists only in the explicitly labeled browser test.

## Implementation

- Source manager: allowlisted-path entry, server-returned locations/errors, enabled toggle, rescan, native keyboard-modal removal confirmation, original-file retention explanation.
- Polling: actual status/phase/counts, activity jobs and progress, ready/ready-with-gaps/empty/updating/blocked/unauthorized/offline states. Published revision equality gates graph and documents. Mutations clear evidence immediately, report server acceptance or persistent errors, and refresh state.
- Explore: bounded actual graph data, document-ID click navigation, fit/pause/labels controls, reduced-motion initial pause, searchable paginated document alternative, source passages and locators, passage continuation, bounded relationship counts. Layout is explicitly not evidence.
- Settings: server-provided safe configuration and copyable `codex_instructions`; Open in Codex opens instructions, not a guessed URI. Codex provider-data caveat is visible.
- Auth: fragment removed synchronously before token POST; cookie credentials included on same-origin API requests; both initial bootstrap and mounted-page hash changes work. Unauthorized guidance uses `./docworm open`.
- Runtime: explicitly instantiate a locally bundled DuckDB worker/WASM, load API records into Arrow tables, and pass that local connection to Cosmograph. External connections do not auto-upload JavaScript arrays; this was caught and corrected in browser verification. Graph lifecycle waits for initialization/config updates before teardown.
- Cosmograph attribution and complete upstream license are served at `/cosmograph-license.txt`.

## Checks actually run

- `npm run typecheck` — passed.
- `npm run test` — 10 tests across 6 files passed: API rejection/auth status/credentials, fragment stripping, readiness revision gate, confirmation, stale inspector rejection, mounted-page bootstrap, and a 15-second bounded worker-startup failure, and readable/malformed relationship handling.
- `npm run build` — passed. Main app about 181 KB; lazy Cosmograph bundle about 1.94 MB; local DuckDB WASM about 34.2 MB. Vite warns about the large lazy graph chunk; runtime assets remain local.
- `npm run format:check` — passed.
- `npm audit --audit-level=moderate` — zero vulnerabilities in the final pinned lockfile.
- `python tests/browser-smoke.py` against built assets on an isolated loopback fixture server — passed. Real Cosmograph rendered 64 synthetic nodes/64 edges, actual graph label selected the correct source, fit/pause/labels worked, list search/empty results worked, source add/toggle/rescan/removal and Escape confirmation worked, Codex instructions loaded, mobile width had no horizontal overflow, published evidence cleared on updating, blocked/empty/unauthorized/offline states worked, and mounted-page token bootstrap worked. No external requests or uncaught page errors in Chromium.

The browser script requires Python Playwright and `/usr/bin/chromium`. It starts an isolated static server for the built assets with strict CSP automatically; override `CHROMIUM` or `DOCWORM_UI_URL` when needed. The final suite passed with `wasm-unsafe-eval` and without general `unsafe-eval`. All API responses in that script are explicitly synthetic; it does not test the actual backend, corpus, source filesystem operations, or model preparation.

## Visual evidence and fidelity ledger

Accepted references inspected with `view_image` before coding: `.swarm/product/design/explore.png` and later `.swarm/product/design/sources.png`. Latest implementation screenshots also inspected with `view_image`; desktop was captured at the reference's 1536×1024 dimensions, mobile at 390×844 with full-page capture.

Retained evidence: `qa/explore-desktop.png`, `qa/sources-desktop.png`, `qa/explore-mobile.png`.

1. Navy background and sidebar, violet selected navigation/primary actions, teal semantic accents: reproduced as CSS tokens; no remote fonts/assets.
2. Sidebar width, heading hierarchy, search/status band, top-right actions, graph toolbar, right source inspector: preserved; mobile uses horizontal navigation and stacked details.
3. Source list retains the reference's Source/Type/Status/Files/Actions table structure and explicit actions. Error copy is the server response.
4. Inspector uses violet file treatment, source path, passage panels and source locators; readable relationship type/status/observed-reference cards link to connected documents, with raw evidence collapsed in optional details. No invented email dates, senders, or quotation evidence.
5. Graph label backgrounds were removed, node sizes improved, and fit behavior adjusted after screenshot comparison. Actual topology determines position and count; the concept's decorative node clusters were deliberately not fabricated.
6. Typography/controls were explicitly styled, keyboard focus is visible, removal uses native modal focus trapping/Escape, and source details receive focus after selection.
7. Copy comparison: primary Explore heading, subtitle, navigation, actions and layout warning match. The search input intentionally says Search document names and the list explains that evidence questions belong in Open in Codex, matching the filename/path-filter API rather than implying semantic search. Intentional additions are accessible Documents navigation, snapshot/bounded counts, actual phase/error/gap messages, source locators and honest Codex instructions. Workspace/metrics/passages come from API responses. “All data stays on your machine” changed to “Indexed on your machine” per coordinator because Codex can transmit selected evidence.

Intentional visual differences: native browser chrome is not recreated; a local outline network brand glyph replaces the concept-only logo; source entry is an inline labeled form rather than the reference's additive modal; metadata absent from the API is omitted; graph nodes/edges and layout follow actual data. The palette, hierarchy and main dashboard structure were visually verified against the references; this is not a claim of pixel-identical static artwork.

## Integration boundaries

Coordinator owns backend/session/demo integration. At their follow-up request this worker also verified the actual API at localhost:8765 in a separate authenticated Chromium session, without printing the bootstrap token or mutating source state. The real API reported Ready, 8 documents, 8 passages, and 3 connections; Fit graph enabled, an actual graph label opened its actual source inspector, graph notice was empty, and DuckDB reported no query error. Evidence: `qa/actual-explore.png`. This uses the lead-installed fictional demo collection, not a private-corpus completeness claim.

Read-only contract comparison confirmed `{token}`, `next_offset`, `relationships_total`, `relationships_truncated`, and `settings.codex_instructions`; the UI supports these fields. The coordinator added the narrow `wasm-unsafe-eval` directive to their API CSP after the initial failure. General JavaScript `unsafe-eval` remains disallowed.

Two CSP incompatibilities were fixed entirely through supported UI configuration, without modifying third-party code or relaxing JavaScript CSP: Arrow `tableFromJSON -> vectorFromArray -> createIsValidFunction` generated `new Function` for null sentinels, so the adapter now uses explicit typed builders with no null sentinels over known non-null columns; Cosmograph default link-count aggregation also called Arrow `vectorFromArray`, so link color/width use single strategies and point properties use direct strategies. Full graph/data/labels/controls pass the strict-CSP browser suite. Actual absolute-path graph labels show their source basename while preserving full paths in the inspector. Links were brightened and the legend only lists node kinds actually present.

Orca embedded browser was used first. Chromium was used for isolated runtime diagnosis and acceptance after embedded-engine errors. Actual Orca-engine compatibility remains for coordinator verification. The two actual 404 messages were traced exactly to `/favicon.ico`, not graph runtime assets; a bundled SVG favicon and explicit icon link fix them. The final actual run had no 404s or aborted WASM request; only the expected initial unauthorized status before bootstrap and software-WebGL performance warnings remained. Actual readable relationship display and connected-document navigation also passed. All ten unit tests, typecheck, build, formatting, and strict-CSP synthetic workflow pass. Synthetic checks establish mechanics; neither the demo nor UI checks prove private-corpus completeness or ingestion correctness.
