#!/bin/sh
set -eu
# Compose file-backed secrets retain host ownership. Copy into a private tmpfs
# before the official entrypoint drops to the postgres operating-system user.
install -d -m 0700 -o postgres -g postgres /run/evidencekg
install -m 0400 -o postgres -g postgres /run/secrets/runtime_password /run/evidencekg/runtime-password
exec /usr/local/bin/docker-entrypoint.sh "$@"
