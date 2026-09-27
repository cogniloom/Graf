# UI screenshots

These are unmodified browser captures of the current Svelte Graf source build, taken on
27 September 2026 at 1440 × 1040. All documents come from the bundled fictional
[demonstration collection](../../../examples/demo/README.md). No private evidence
or authentication token is visible.

- `graph.png`: the workspace graph rendered by the actual local Cosmograph engine.
- `investigation.png`: an actual Codex subscription-backed answer with exact-quote
  checks and a generated Markdown report retained in Graf.
- `new-session.png`: the research composer and persistent session history.
- `sources.png`: the indexed demo directory and source-management controls.

The capture workspace used real ingestion and PostgreSQL snapshot storage, with
deterministic candidate retrieval substituted for model ranking. These images
illustrate the interface; they are not performance or retrieval-quality evidence.

To refresh them, build `product/ui`, start an isolated workspace with `examples/demo`,
complete an investigation, and capture the corresponding pages in Chromium. Never
use a private corpus for public documentation screenshots. See the
[verification guide](../../VERIFICATION.md) for the verification boundary.
