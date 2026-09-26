# Operations and recovery

## Daily commands

```sh
./docworm start
./docworm status
./docworm doctor
./docworm stop
./docworm stop --database
```

Stop preserves the database volume, models, source originals and private generations. Application logs are in `<home>/logs/application.log`; PostgreSQL has bounded Docker logs. Do not publish logs without checking for document names, excerpts and errors. The application runs as your user. Docker restarts PostgreSQL automatically; the native application must be started again after logout/reboot. An unattended service manager is not installed automatically.

Activity shows failed and recovered jobs. After fixing an unavailable source or model dependency, rescan the source or use the dashboard rebuild action. A dead worker's current job is recovered by the next worker; stale revisions cannot become current publications. Failures remain visible rather than silently returning an old snapshot.

## Back up

```sh
./docworm backup /absolute/private-backups/2026-09-26
./docworm verify-backup /absolute/private-backups/2026-09-26
```

The launcher stops the API, takes a PostgreSQL custom-format dump, copies private generations/configuration/credentials, writes file checksums, and restarts an application that was running. The destination must be new and outside the workspace. Failed bundles contain `INCOMPLETE` and must not be used. Keep backups private and preferably encrypted at rest; they contain source-derived evidence and credentials. Original source directories, downloaded models and the Python environment are not included: back up originals separately and retain the pinned model revisions.

Verification hashes the files and restores the dump into a uniquely named temporary database, checks its workspace row and removes only that temporary database. It does not overwrite the live workspace or prove semantic accuracy.

## Restore after loss

Restore is an administrator operation, intentionally not a one-click overwrite. First run verify-backup against a healthy installation. Stop the application. Preserve any existing workspace and database before proceeding. Restore configuration and generations at the **same absolute home path** recorded in the manifest: stored publication identities and artifact paths are bound to that path. Recreate the Compose service with the backed-up secrets and an empty database volume, then use `pg_restore --exit-on-error --no-owner` through `docker compose exec -T postgres` against the empty `evidencekg` database. Do not use `--clean` on a database you have not independently identified as disposable.

Reinstall the locked Python runtime and pinned model weights, check external source roots, run migrations through the current launcher update path, then start and rebuild. Verify current sources and a cited query before relying on the recovered workspace. Cross-path migration and automatic retention/purge are not supported in this release.

## Update

Back up and verify first. Extract a new release at a stable application path, then run `./docworm update` for the existing home. It stops the API, synchronizes the locked runtime, migrates schemas, starts the API and requests a rebuild. Source releases need `npm ci && npm run build` in `product/ui` before updating. Do not interrupt schema migration. Re-run plugin-install so Codex uses the current bridge.

## Troubleshooting

- **Docker unavailable:** verify Docker/Compose independently; the installer never changes socket permissions.
- **Missing models:** run `./docworm models`. Download access is needed only to obtain pinned weights.
- **CUDA unavailable or out of memory:** stop, choose `device: "cpu"` in private app.json, start and rebuild. CPU may be substantially slower.
- **Blocked extraction:** inspect the job error and document warnings. Scanned images require a working Tesseract installation and suitable language data. Unsupported/oversized/locked documents remain coverage gaps.
- **Graph unavailable:** check WebGL support and use the document list. Local graph worker/WASM files must be present in the built UI assets.
- **Source denied:** authorize its parent with allow-root; symlinks and overlapping sources are deliberately rejected.
- **Application exits:** inspect the final log messages and run doctor. Never paste passwords, sign-in fragments or complete connection strings into an issue.

To uninstall without losing evidence, stop with `--database`, remove the Codex plugin and archive the private workspace. Remove application files only after preserving backups. Docker volumes are not automatically deleted.
