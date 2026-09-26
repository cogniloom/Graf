# Codex integration

Install and start the local application first. Use a Codex CLI version with `codex plugin marketplace` and `codex plugin add` support. Run:

```sh
./docworm plugin-install
```

This registers the repository's local marketplace, installs its `docworm` plugin and writes a private runtime pointer under `$XDG_CONFIG_HOME/docworm/runtime.json` (normally `~/.config/docworm/runtime.json`). Open a new Codex thread to load the tools and skills. Keep the extracted application directory in place; the pointer identifies its bridge script. Re-run plugin-install after moving or upgrading it.

For multiple workspaces, install the pointer for your preferred workspace, or launch Codex with `DOCWORM_HOME=/absolute/workspace`. The plugin is a thin stdio MCP bridge to the running local API. It does not start another model, store Codex credentials or require an API key. If tools cannot connect, run `./docworm start` and `./docworm doctor` for that workspace.

Tools include workspace status, source listing/add/remove/rescan, discovery, workset continuation, document listing/reading and bounded graph inspection. The investigation skill checks readiness, requests connected context, looks for contrary evidence and cites source passages. The management skill acts on source changes only when requested.

The registry format and commands are based on the installed Codex plugin specification; see the [official plugin documentation](https://developers.openai.com/plugins). This is a local Codex integration, not a hosted ChatGPT connector. GitHub publication and Codex directory submission are separate actions.

## Privacy boundary

Parsing, graph construction, embeddings and reranking run locally. When Codex reads tool results, selected evidence becomes part of the Codex conversation and may be sent to its configured model provider. Account, retention and subscription policies remain those of that provider. Docworm does not automatically switch to API-key billing or invoke third-party inference.

Source text is untrusted data. The skill explicitly rejects document-borne instructions and separates evidence from interpretation. A skill instruction is not a security sandbox: use appropriate Codex permissions and human review.

To remove the plugin, use Codex's plugin management interface or `codex plugin remove --help` for your installed CLI. Stopping Docworm does not delete evidence. Do not publish runtime pointers, token files or private app configuration.
