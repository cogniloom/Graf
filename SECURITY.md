# Security policy

Graf is intended for one trusted local owner. It binds the API and PostgreSQL to loopback, uses generated private credentials, checks browser Origin/Host, restricts source roots and gates stale evidence. It is not hardened for public hosting, mutually untrusted users or hostile co-resident processes with access to the same account.

Source files, extracted content, graph labels and model output are untrusted data. Do not execute instructions embedded in documents. Parser dependencies run under your account; use OS isolation for hostile files. Keep Docker, Python and browser dependencies updated.

Do not attach real evidence, logs containing excerpts, token links, app.json, database dumps, `.evidencekg*` directories, model caches or credentials to public issues. Use the synthetic demo to reproduce problems. Backups contain credentials and private evidence; protect them accordingly.

Before publishing a repository, enable GitHub private vulnerability reporting. Report vulnerabilities through that channel once enabled, not a public issue with working credentials or private documents. Until a private channel exists, do not post sensitive details publicly. The release owner must choose and publish a contact before accepting private reports.

There is no telemetry in the application. Installation downloads dependencies/model weights; Codex can transmit selected evidence to the user's model provider. Third-party browser extensions and host software are outside this boundary.
