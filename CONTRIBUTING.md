# Contributing

Use synthetic data only. Start with docs/ARCHITECTURE.md and docs/VERIFICATION.md. Keep source originals immutable, distinguish inferred from explicit relationships, preserve retrieval provenance and fail closed when a publication no longer matches its source revision.

Install development dependencies with `uv sync --project evidencekg --extra app`; run focused pytest tests before the broader suite. The real PostgreSQL tests require an explicitly selected disposable database environment. Never point destructive fixtures at a user's workspace. Build the UI with `npm ci`, `npm test`, and `npm run build` in product/ui.

New changes must include relevant failure-path checks and actual UI interaction checks when applicable. Do not claim semantic accuracy from mocked model tests or a successful build. Do not invoke external inference, resume private benchmarks, or add billing fallback as part of routine tests.

Dependency updates must retain license notices and offline UI assets. Commercial alternatives to Cosmograph can be implemented behind the graph interface, but must not silently change the license of the bundled component.
