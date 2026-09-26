"""Create new local Compose credentials without displaying or replacing secrets."""

import json
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[2]
directory = root / ".evidencekg-private/postgres"
os.umask(0o077)
directory.mkdir(parents=True, exist_ok=True)
for name in ("admin-password", "runtime-password"):
    path = directory / name
    if not path.exists():
        with path.open("x") as out:
            out.write(secrets.token_urlsafe(36))
for role, password in (("postgres", "admin-password"), ("evidencekg", "runtime-password")):
    config = directory / (role + ".json")
    value = {
        "host": "127.0.0.1",
        "port": int(os.environ.get("EVIDENCEKG_PG_PORT", "55432")),
        "dbname": "evidencekg",
        "user": role,
        "password_file": str(directory / password),
    }
    if not config.exists():
        with config.open("x") as out:
            json.dump(value, out)
print("Local Compose credential files ready; existing files preserved.")
