#!/bin/sh
set -eu
runtime_password="$(cat /run/evidencekg/runtime-password)"
[ -n "$runtime_password" ]
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=runtime_password="$runtime_password" <<'SQL'
CREATE ROLE evidencekg LOGIN PASSWORD :'runtime_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE evidencekg TO evidencekg;
GRANT USAGE ON SCHEMA public TO evidencekg;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, INSERT ON TABLES TO evidencekg;
SQL
