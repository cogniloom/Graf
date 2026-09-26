---
name: manage-sources
description: Add, remove or rescan Graf source files and directories and explain ingestion progress, readiness and extraction failures.
---

Read workspace_status and list_sources. Use add_source, remove_source or rescan_source only when the user's request calls for that change. Paths must fall under roots already allowed by the local owner. If a root is missing, explain the local command `./graf allow-root /absolute/directory`; do not bypass the allowlist.

Removal excludes that source from subsequent published searches and never deletes the original file. Historical evidence is retained locally for audit; removal is not secure erasure. Any source revision change temporarily gates evidence endpoints until publication catches up. Do not promise immediate readiness or invent percentages. Report actual phase, jobs, errors and counts, including ready_with_gaps. Failed indexing is not an empty corpus.

To open the dashboard, tell the user to run `./graf open`. Do not display or request session tokens, database passwords, or private sign-in URLs. A browser's folder path does not authorize arbitrary access outside the configured roots.
