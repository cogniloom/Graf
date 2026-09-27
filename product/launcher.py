"""Graf local installation and service lifecycle. Standard-library bootstrap."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_IMAGE = "postgres:17@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f"


def run(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, **kwargs)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w") as out:
        json.dump(value, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)


def config(home):
    path = home / "app.json"
    if not path.is_file():
        raise ValueError("Workspace is not installed. Run ./graf install first.")
    return json.loads(path.read_text())


def compose(home, *args, **kwargs):
    return run(["docker", "compose", "-f", home / "compose.json", *args], **kwargs)


def free_port(preferred):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def subnet():
    ids = run(["docker", "network", "ls", "-q"], capture_output=True, text=True).stdout.split()
    occupied = []
    if ids:
        rows = json.loads(run(["docker", "network", "inspect", *ids], capture_output=True, text=True).stdout)
        for row in rows:
            for entry in row.get("IPAM", {}).get("Config", []) or []:
                if entry.get("Subnet"):
                    occupied.append(ipaddress.ip_network(entry["Subnet"]))
    if shutil.which("ip"):
        routes = json.loads(run(["ip", "-j", "-4", "route"], capture_output=True, text=True).stdout)
        for row in routes:
            if row.get("dst") and row["dst"] != "default":
                occupied.append(ipaddress.ip_network(row["dst"], strict=False))
    for i in range(16, 255):
        candidate = ipaddress.ip_network(f"10.232.{i}.0/24")
        if not any(candidate.version == net.version and candidate.overlaps(net) for net in occupied):
            return str(candidate)
    raise ValueError("No unused private Compose subnet found. Configure compose.json explicitly.")


def api(home, path, method="GET", body=None):
    cfg = config(home)
    token = Path(cfg["token_file"]).read_text().strip()
    request = urllib.request.Request(
        f"http://127.0.0.1:{cfg['port']}" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def runtime(home):
    return Path(config(home)["python"])


def alive(home):
    try:
        record = json.loads((home / "service.json").read_text())
        pid = record["pid"]
        args = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        return pid if b"evidencekg.app" in args and str(home / "app.json").encode() in args else None
    except (OSError, ValueError, KeyError):
        return None


def start(home):
    cfg = config(home)
    if alive(home):
        print("Graf is already running.")
        return
    compose(home, "up", "-d", "--wait", "postgres")
    (home / "logs").mkdir(exist_ok=True)
    with (home / "logs" / "application.log").open("ab") as log:
        proc = subprocess.Popen(
            [str(runtime(home)), "-m", "evidencekg.app", "--config", str(home / "app.json")],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "evidencekg/src"),
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
        )
    write_json(home / "service.json", {"pid": proc.pid, "started_at": time.time()})
    for _ in range(120):
        if proc.poll() is not None:
            raise ValueError("Application exited; inspect " + str(home / "logs/application.log"))
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{cfg['port']}/healthz", timeout=1) as response:
                if response.status == 200:
                    print(
                        f"Graf is running at http://127.0.0.1:{cfg['port']}. Run ./graf open to sign in."
                    )
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise ValueError("Application did not become healthy; inspect its local log.")


def stop(home, database=False):
    pid = alive(home)
    if pid:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(120):
            if not alive(home):
                break
            time.sleep(0.5)
        else:
            raise ValueError("Application has not stopped yet; no force kill was issued.")
    if database:
        compose(home, "stop", "postgres")
    print("Stopped; documents, models and database volume preserved.")


def install(args, home):
    if sys.platform != "linux":
        raise ValueError(
            "This release supports Linux. Use WSL2 on Windows; native macOS/Windows are not verified."
        )
    for executable in ("docker", "uv"):
        if not shutil.which(executable):
            raise ValueError(f"Install {executable} first; see docs/INSTALL.md.")
    if (home / "app.json").exists():
        if not (home / "installed.json").exists():
            compose(home, "up", "-d", "--wait", "postgres")
            migrate(home)
            start(home)
            write_json(home / "installed.json", {"version": "0.1.0"})
            print("Interrupted installation completed. Add sources in the dashboard.")
            return
        raise ValueError("Workspace already installed. Use update, start or a different --home.")
    if any(not 1024 <= value <= 65535 for value in (args.port, args.database_port)):
        raise ValueError("Choose ports from 1024 to 65535")
    if home.stat().st_mode & 0o077:
        raise ValueError("Workspace home must be private (mode 0700)")
    run(["docker", "compose", "version"], stdout=subprocess.DEVNULL)
    run(["docker", "info", "--format", "{{.ServerVersion}}"], stdout=subprocess.DEVNULL)
    roots = [str(Path(p).expanduser().resolve(strict=True)) for p in args.allow_root]
    if any(not Path(p).is_dir() for p in roots):
        raise ValueError("--allow-root requires directories")
    source_home = home.with_name(home.name + "-sources")
    if args.demo:
        shutil.copytree(ROOT / "examples/demo", source_home / "demo", dirs_exist_ok=True)
        roots.append(str(source_home / "demo"))
    if not roots:
        (source_home / "inbox").mkdir(parents=True, exist_ok=True)
        roots = [str(source_home / "inbox")]
    port = free_port(args.port)
    pg_port = free_port(args.database_port)
    if port == pg_port:
        raise ValueError("Application and database ports must differ")
    private = home / "secrets"
    private.mkdir(exist_ok=True)
    for name in ("admin-password", "runtime-password", "session-token"):
        path = private / name
        if not path.exists():
            with path.open("x") as out:
                out.write(secrets.token_urlsafe(36))
    for user, password in [("postgres", "admin-password"), ("evidencekg", "runtime-password")]:
        write_json(
            home / (user + ".json"),
            {
                "host": "127.0.0.1",
                "port": pg_port,
                "dbname": "evidencekg",
                "user": user,
                "password_file": str(private / password),
            },
        )
    compose_value = {
        "name": "graf-" + hashlib.sha256(str(home).encode()).hexdigest()[:12],
        "services": {
            "postgres": {
                "image": PG_IMAGE,
                "entrypoint": ["/bin/sh", "/opt/evidencekg-entrypoint.sh"],
                "command": ["postgres"],
                "tmpfs": ["/run/evidencekg"],
                "restart": "unless-stopped",
                "environment": {
                    "POSTGRES_DB": "evidencekg",
                    "POSTGRES_USER": "postgres",
                    "POSTGRES_PASSWORD_FILE": "/run/secrets/admin_password",
                    "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
                },
                "secrets": ["admin_password", "runtime_password"],
                "ports": [f"127.0.0.1:{pg_port}:5432"],
                "volumes": [
                    "database:/var/lib/postgresql/data",
                    str(ROOT / "deploy/postgres/entrypoint.sh") + ":/opt/evidencekg-entrypoint.sh:ro",
                    str(ROOT / "deploy/postgres/init.sh") + ":/docker-entrypoint-initdb.d/10-runtime.sh:ro",
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U postgres -d evidencekg"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 12,
                    "start_period": "20s",
                },
                "stop_grace_period": "60s",
                "shm_size": "256mb",
                "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
            }
        },
        "volumes": {"database": {}},
        "networks": {"default": {"ipam": {"config": [{"subnet": subnet()}]}}},
        "secrets": {
            "admin_password": {"file": str(private / "admin-password")},
            "runtime_password": {"file": str(private / "runtime-password")},
        },
    }
    write_json(home / "compose.json", compose_value)
    if args.runtime:
        python = Path(args.runtime).expanduser().absolute()
        if not python.is_file():
            raise ValueError("Python runtime does not exist")
    else:
        run(
            [
                "uv",
                "sync",
                "--project",
                ROOT / "evidencekg",
                "--frozen",
                "--extra",
                "app",
                "--extra",
                "hybrid",
                "--no-dev",
            ],
            env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(home / "runtime")},
        )
        python = home / "runtime/bin/python"
    ui = ROOT / "product/ui/dist"
    if not (ui / "index.html").is_file():
        if shutil.which("npm"):
            run(["npm", "ci"], cwd=ROOT / "product/ui")
            run(["npm", "run", "build"], cwd=ROOT / "product/ui")
        else:
            raise ValueError(
                "UI assets missing. Use the release archive or install Node.js and npm, then rerun installation."
            )
    models = Path(args.models).expanduser().resolve() if args.models else home / "models"
    if not args.skip_model_download and not args.models:
        run(
            [python, "-m", "evidencekg.hybrid.download_models", models],
            env={**os.environ, "PYTHONPATH": str(ROOT / "evidencekg/src")},
        )
    cfg = {
        "home": str(home),
        "workspace_name": "Demo collection" if args.demo else "My workspace",
        "database_config": str(home / "evidencekg.json"),
        "models": str(models),
        "device": args.device,
        "allowed_roots": roots,
        "host": "127.0.0.1",
        "port": port,
        "token_file": str(private / "session-token"),
        "ui_dist": str(ui),
        "scan_interval_seconds": 30,
        "python": str(python),
        "mcp_bridge": str(ROOT / "plugins/graf/scripts/server.py"),
    }
    write_json(home / "app.json", cfg)
    compose(home, "up", "-d", "--wait", "postgres")
    migrate(home)
    start(home)
    if args.demo:
        api(home, "/api/sources", "POST", {"path": str(source_home / "demo")})
    write_json(home / "installed.json", {"version": "0.1.0"})
    print("Installed. Models and indexing progress are visible in the dashboard.")


def migrate(home):
    program = """from evidencekg.hybrid.runtime import database_dsn
from evidencekg.hybrid.postgres import migrate as graph_migrate
from evidencekg.app import migrate as app_migrate
from psycopg import connect, sql
import sys
dsn=database_dsn(sys.argv[1]);graph_migrate(dsn);app_migrate(dsn)
with connect(dsn) as db:
    for name in ('docworm_workspace','docworm_sources','docworm_jobs','docworm_worksets'):
        db.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON {} TO evidencekg').format(sql.Identifier(name)))
"""
    run(
        [runtime(home), "-c", program, home / "postgres.json"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "evidencekg/src")},
    )


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(prog="graf", description="Graf local evidence workspace")
    p.add_argument(
        "--home",
        type=Path,
        default=Path(
            os.environ.get(
                "GRAF_HOME",
                Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "graf",
            )
        ),
    )
    commands = p.add_subparsers(dest="action", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--allow-root", action="append", default=[])
    setup.add_argument("--demo", action="store_true")
    setup.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    setup.add_argument("--models", type=Path)
    setup.add_argument("--skip-model-download", action="store_true")
    setup.add_argument(
        "--runtime",
        type=Path,
        help="Advanced: use a prepared Python runtime instead of installing dependencies",
    )
    setup.add_argument("--port", type=int, default=8765)
    setup.add_argument("--database-port", type=int, default=55432)
    for name in ("start", "status", "doctor", "models", "update", "plugin-install"):
        commands.add_parser(name)
    commands.add_parser("allow-root").add_argument("path", type=Path)
    for name in ("backup", "verify-backup"):
        commands.add_parser(name).add_argument("directory", type=Path)
    commands.add_parser("stop").add_argument("--database", action="store_true")
    opening = commands.add_parser("open")
    opening.add_argument("--print-url", action="store_true")
    source = commands.add_parser("sources")
    subs = source.add_subparsers(dest="source_action", required=True)
    subs.add_parser("list")
    subs.add_parser("add").add_argument("path")
    for action in ("remove", "rescan", "pause", "resume"):
        subs.add_parser(action).add_argument("id")
    args = p.parse_args()
    home = args.home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    with (home / "launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == "install":
            install(args, home)
        elif args.action == "allow-root":
            print("All local source locations are available. Select a file or folder in Sources.")
        elif args.action in ("backup", "verify-backup"):
            from backup import backup, verify

            if args.action == "backup":
                was_running = bool(alive(home))
                stop(home)
                try:
                    backup(home, args.directory, compose)
                finally:
                    if was_running:
                        start(home)
            else:
                verify(home, args.directory, compose)
        elif args.action == "start":
            start(home)
        elif args.action == "stop":
            stop(home, args.database)
        elif args.action == "status":
            print(json.dumps(api(home, "/api/status"), indent=2))
        elif args.action == "doctor":
            cfg = config(home)
            compose(home, "ps")
            print(
                json.dumps(
                    {
                        "runtime_exists": runtime(home).is_file(),
                        "ui_exists": (Path(cfg["ui_dist"]) / "index.html").is_file(),
                        "models_path": cfg["models"],
                        "application_process": bool(alive(home)),
                    },
                    indent=2,
                )
            )
            print(json.dumps(api(home, "/api/status"), indent=2))
        elif args.action == "open":
            cfg = config(home)
            url = f"http://127.0.0.1:{cfg['port']}/#token=" + Path(cfg["token_file"]).read_text().strip()
            if args.print_url:
                print(url)
            elif not webbrowser.open(url):
                raise ValueError(
                    "No browser opened. Use open --print-url and keep that sign-in link private."
                )
        elif args.action == "models":
            cfg = config(home)
            run(
                [runtime(home), "-m", "evidencekg.hybrid.download_models", cfg["models"]],
                env={**os.environ, "PYTHONPATH": str(ROOT / "evidencekg/src")},
            )
            api(home, "/api/rebuild", "POST", {})
        elif args.action == "sources":
            if args.source_action == "list":
                value = api(home, "/api/sources")
            elif args.source_action == "add":
                value = api(
                    home,
                    "/api/sources",
                    "POST",
                    {"path": str(Path(args.path).expanduser().resolve(strict=True))},
                )
            elif args.source_action == "remove":
                value = api(home, "/api/sources/" + args.id, "DELETE")
            elif args.source_action == "rescan":
                value = api(home, "/api/sources/" + args.id + "/rescan", "POST", {})
            else:
                value = api(
                    home, "/api/sources/" + args.id, "PATCH", {"enabled": args.source_action == "resume"}
                )
            print(json.dumps(value, indent=2))
        elif args.action == "update":
            stop(home)
            run(
                [
                    "uv",
                    "sync",
                    "--project",
                    ROOT / "evidencekg",
                    "--frozen",
                    "--extra",
                    "app",
                    "--extra",
                    "hybrid",
                    "--no-dev",
                ],
                env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(home / "runtime")},
            )
            cfg = config(home)
            cfg["python"] = str(home / "runtime/bin/python")
            cfg["ui_dist"] = str(ROOT / "product/ui/dist")
            cfg["mcp_bridge"] = str(ROOT / "plugins/graf/scripts/server.py")
            write_json(home / "app.json", cfg)
            migrate(home)
            start(home)
            api(home, "/api/rebuild", "POST", {})
        elif args.action == "plugin-install":
            cfg = config(home)
            target = home / "plugin-runtime.json"
            write_json(target, {"python": cfg["python"], "config": str(home / "app.json")})
            # The plugin launch helper reads this per-user pointer, not credentials.
            pointer = (
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "graf/runtime.json"
            )
            write_json(
                pointer,
                {
                    "config": str(home / "app.json"),
                    "python": cfg["python"],
                    "bridge": str(ROOT / "plugins/graf/scripts/server.py"),
                },
            )
            run(["codex", "plugin", "marketplace", "add", ROOT])
            marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())["name"]
            run(["codex", "plugin", "add", "graf@" + marketplace])
            print("Plugin installed. Open a new Codex thread to load its skills and tools.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, urllib.error.HTTPError) as exc:
        print("Graf: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
