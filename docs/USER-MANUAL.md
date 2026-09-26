# User manual

## Open and sign in

Run `./graf open`. A short-lived browser navigation carries your private local token in a URL fragment, exchanges it for an HttpOnly session cookie, and removes the fragment. Do not share sign-in links. `./graf open --print-url` is an explicit fallback when no browser launcher is available; its output is a credential.

## Add and manage evidence

In **Sources**, add an absolute file or directory path within an allowed root. Directories are scanned recursively. Files are hashed, copied into a private staging generation, parsed and indexed. Originals are never modified. Avoid registering overlapping sources; register their common parent once instead.

**Rescan** requests a new build. A periodic watcher also checks contents, including changes that preserve file size and modification time. **Pause** excludes a source from the next published revision; **Resume** includes it again. **Remove** unregisters a source and rebuilds the remaining collection. This is not secure erasure: old private generations and audit data remain until a separately planned retention procedure removes them.

To authorize a new location, run `./graf allow-root /absolute/directory`. You can also manage sources using `./graf sources list`, `add PATH`, `rescan ID`, `pause ID`, `resume ID` and `remove ID`.

## Understand readiness

| State | Meaning |
|---|---|
| Empty | No active evidence is published |
| Updating | A source revision is being inventoried, extracted, indexed or validated |
| Ready | The current source revision is published |
| Ready with gaps | Published evidence is searchable, but extraction or coverage warnings remain |
| Blocked | A job failed; inspect Activity and the underlying error |

Evidence endpoints check that the requested revision is still current. During updates, old search results are unavailable. Processing counts and phases are real; the application does not manufacture an overall completion percentage. A successful process cannot prove semantic completeness.

## Explore the graph

Use the graph controls to fit the view, pause motion and show labels. Select a node to inspect its source document and passages. Search the document list when the graph is crowded or when using a keyboard/screen reader. The visualization is bounded; check the shown totals and truncation notice. A central or brightly colored node is not inherently stronger evidence.

Cosmograph requires a working WebGL browser. If graphics are unavailable, the document list and evidence tools remain the practical access path. Relationship types and source text determine meaning, not the layout.

## Investigate

The dashboard search filters document names/paths. Use **Open in Codex** and the Graf tools for semantic evidence questions, ranking and retained candidate queues. A filename search with no matches does not mean no relevant evidence exists.

Ask a precise question, preserve identifiers, and inspect connected replies, attachments and versions. Continue candidate queues when needed. A highly ranked passage can be incomplete or contradicted elsewhere. The fictional demo deliberately includes conditional approval, an order placed before a certificate was issued, and conflicting drafts.

Codex can summarize and reason over retrieved evidence. Check citations against the source passages. Distinguish quoted statements from inference; do not treat this application as a guarantee of legal correctness.
