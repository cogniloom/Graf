# Graf workbench verification

The frontend uses Svelte 5, TypeScript, and Vite. React and its plugins have been
removed. The graph is the real, locally bundled Cosmograph 2.5.1 engine with DuckDB
WebAssembly; its CC BY-NC 4.0 attribution remains visible.

## Design and implementation

The workbench uses a warm paper palette, ink typography, fine rules, and a restrained
rust accent. A compact navigation rail, persistent session list, reading pane, and
session inspector replace the previous dark card layout. A simple typographic Graf wordmark fits the narrow rail. The full illustrated
logo remains in the repository and README, where it has room to breathe. Desktop panes become stacked sections on narrow screens.

The local concept reference is `.swarm/design/workbench-concept.png` (not shipped).
The generated visual concept guided the pane proportions, serif document typography,
compact controls, and border treatment. Actual browser captures were inspected
against it. Intentional differences: real prompts, timestamps, citations and counts replace its
illustrative content; the miniature graph uses actual recorded nodes and edges.
The complete initial prompt remains accessible beneath the shortened heading.

The source API, local authentication, subscription adapter, ledger, and erasure
contracts remain unchanged by this frontend migration. Controls still expose
partial-index consent, source lifecycle, session activity, provenance, generated
outputs, attributed notes/revisions, withdrawal, comparison, erasure previews,
package exports, and signed checkpoints. Generated content is displayed as escaped
text; HTML payloads cannot execute in previews.

## Checks

- `npm test`: 18 tests cover API/authentication, mounted-page bootstrap, source
  removal confirmation, stale document and artifact responses, erasure invalidation,
  graph polling stability, quotation/source coverage, literal HTML previews,
  incomplete-collection consent, graph startup timeout, and relationship details.
- `npm run build`: Svelte/TypeScript diagnostics and production build.
- `npm run format:check`: source formatting.
- `tests/browser-smoke.py`: migrated synthetic strict-CSP browser journey for real
  Cosmograph rendering and label selection, document navigation, source add/toggle/
  rescan/removal, Escape cancellation, settings, mobile width, readiness invalidation,
  unauthorized/offline recovery, and bootstrap fragment removal.
- Real local API acceptance uses the fictional demo collection and a previously
  completed Codex subscription session. It checks original/generated previews,
  erasure impact confirmation without deleting evidence, graph highlight/fullscreen,
  retained canvas across polling, package download, follow-up composition, and a
  rejected source path. No external browser requests or uncaught page errors.
- The downloaded evidence ZIP passed the offline verifier with a separately read
  local public-key pin (`valid`, `integrity`, and `trusted` all true).
- Distribution: 15 tests pass; a locally built release archive contains the Svelte
  configuration, source files, and the typographic favicon. Nothing was published.
- README captures use Chromium at 1440 × 1040. Sources, sessions, and session graphs
  were also checked at 390px without horizontal page overflow.

The browser script requires Python Playwright and `/usr/bin/chromium`. Run it after
building: `python tests/browser-smoke.py`. It serves an isolated local fixture with
strict CSP and synthetic API responses; it does not test ingestion or a provider.
Real-API screenshot provenance is in [docs/assets/ui](../../docs/assets/ui/README.md).

## Limits

The existing large lazy Cosmograph chunk and approximately 34 MB DuckDB WASM remain.
Screenshots and synthetic checks establish interface behavior, not retrieval quality,
legal admissibility, or provider identity. The demo fixture substitutes deterministic
candidate retrieval for model ranking. Chromium was used because the embedded
browser/Orca runtime was unavailable; this redesign has not been independently
reviewed through an Orca worker or verified in Firefox/Safari.
