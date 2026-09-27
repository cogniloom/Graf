"""Authenticated same-origin, loopback-only HTTP interface for one workspace."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .config import AppConfig, absolute
from .files import browse
from .manager import Manager, Missing, NotReady

COOKIE = "docworm_session"
SESSION_SECONDS = 12 * 60 * 60


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Session(Payload):
    token: str = Field(min_length=32, max_length=4096)


class Source(Payload):
    path: str = Field(min_length=1, max_length=4096)


class SourcePatch(Payload):
    enabled: StrictBool


class Search(Payload):
    question: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=12, ge=1, le=12, strict=True)


class Investigation(Payload):
    prompt: str = Field(min_length=1, max_length=16000)
    allow_partial: StrictBool = False
    parent_id: str | None = Field(default=None, max_length=64)
    request_id: str = Field(min_length=16, max_length=64)
    model: str = Field(default="gpt-6-astra", max_length=64)
    effort: str = Field(default="medium", max_length=16)


class Annotation(Payload):
    text: str = Field(min_length=1, max_length=16000)


class Revision(Payload):
    text: str = Field(min_length=1, max_length=16000)


class Erasure(Payload):
    artifact_ids: list[str] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=4096)
    preview_hash: str | None = Field(default=None, max_length=64)
    confirmation: str | None = Field(default=None, max_length=100)


def create_app(config, *, manager=None, start_background=True, investigations=None):
    config = config if isinstance(config, AppConfig) else AppConfig.load(config)
    token = config.token()
    manager = manager or Manager(config)
    from evidencekg.investigations.service import Investigations

    investigations = investigations or Investigations(manager)
    sessions = {}
    session_lock = threading.Lock()
    hosts = {f"127.0.0.1:{config.port}", f"localhost:{config.port}"}

    @asynccontextmanager
    async def lifespan(app):
        try:
            if start_background:
                investigations.start()
                manager.start()
            yield
        finally:
            if start_background:
                investigations.stop()
                manager.stop()

    app = FastAPI(title="Graf", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager = manager

    def cookie_key(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def authenticated(request):
        auth = request.headers.get("authorization", "")
        if auth:
            return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), token.encode()), True
        cookie = request.cookies.get(COOKIE, "")
        if not cookie:
            return False, False
        with session_lock:
            expires = sessions.get(cookie_key(cookie), 0)
        return expires > time.monotonic(), False

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        # Ignore proxy headers: the entrypoint disables uvicorn proxy trust.
        if host not in hosts or len(request.headers.getlist("host")) != 1:
            return JSONResponse({"detail": "Loopback Host required"}, status_code=400)
        if origin is not None and (origin != "http://" + host or len(request.headers.getlist("origin")) != 1):
            return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
        if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/"):
            valid, bearer = authenticated(request)
            if path != "/api/session" and not valid:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            if request.method not in ("GET", "HEAD") and not bearer and origin != "http://" + host:
                return JSONResponse({"detail": "Same-origin Origin header required"}, status_code=403)
            if request.method in ("POST", "PATCH", "PUT"):
                if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                    # Empty command POSTs such as rebuild/rescan require no JSON body.
                    if path in ("/api/session", "/api/sources", "/api/search") or request.method == "PATCH":
                        return JSONResponse({"detail": "JSON content type required"}, status_code=415)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 32_768:
                        return JSONResponse({"detail": "Request body too large"}, status_code=413)
                request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(psycopg.Error)
    async def database_unavailable(request, exc):
        return JSONResponse(
            {"detail": "Workspace database unavailable; retry when it is restored"}, status_code=503
        )

    @app.post("/api/session")
    def session(body: Session):
        if not hmac.compare_digest(body.token.encode(), token.encode()):
            return JSONResponse({"detail": "Invalid session token"}, status_code=401)
        value = secrets.token_urlsafe(32)
        now = time.monotonic()
        with session_lock:
            for key in [k for k, expiry in sessions.items() if expiry <= now]:
                sessions.pop(key)
            if len(sessions) >= 1024:
                return JSONResponse({"detail": "Too many active sessions"}, status_code=429)
            sessions[cookie_key(value)] = now + SESSION_SECONDS
        response = JSONResponse({"success": True})
        response.set_cookie(
            COOKIE, value, httponly=True, samesite="strict", max_age=SESSION_SECONDS, path="/api"
        )
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        with session_lock:
            sessions.pop(cookie_key(request.cookies.get(COOKIE, "")), None)
        response = JSONResponse({"success": True})
        response.delete_cookie(COOKIE, path="/api", httponly=True, samesite="strict")
        return response

    @app.get("/healthz")
    def health():
        return {"alive": True}

    @app.get("/readyz")
    def ready():
        try:
            manager._ready()
            return {"ready": True}
        except (NotReady, ValueError, OSError, psycopg.Error):
            return JSONResponse({"ready": False}, status_code=503)

    @app.get("/api/status")
    def status():
        manager.reconcile()
        return manager.status()

    @app.get("/api/sources")
    def sources():
        return manager.sources()

    @app.post("/api/sources")
    def register(body: Source):
        return manager.register(body.path)

    @app.delete("/api/sources/{source_id}")
    def remove(source_id: str):
        return manager.change(source_id, remove=True)

    @app.patch("/api/sources/{source_id}")
    def patch(source_id: str, body: SourcePatch):
        return manager.change(source_id, enabled=body.enabled)

    @app.post("/api/sources/{source_id}/rescan")
    def rescan(source_id: str):
        return manager.change(source_id, rescan=True)

    @app.post("/api/rebuild")
    def rebuild():
        return manager.rebuild()

    @app.get("/api/jobs")
    def jobs():
        return manager.jobs()

    @app.get("/api/filesystem")
    def filesystem(
        path: str | None = Query(None, max_length=4096),
        offset: int = Query(0, ge=0),
    ):
        return browse(config, path, offset)

    @app.get("/api/settings")
    def settings():
        return {
            "workspace_name": config.workspace_name or config.home.name,
            "codex_instructions": "Run ./graf plugin-install, then ask Codex to use Graf.",
            "device": config.device,
            "host": config.host,
            "port": config.port,
            "scan_interval_seconds": config.scan_interval_seconds,
            "generative_model_calls": 0,
            "investigation_actor": investigations.actor,
            "identity_boundary": "Authenticated local OS owner; not independently verified legal identity",
        }

    @app.get("/api/graph")
    def graph(limit: int = Query(2000, ge=1, le=5000)):
        return manager.graph(limit)

    @app.get("/api/documents")
    def documents(
        query: str = Query("", max_length=4096),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ):
        return manager.documents(query, offset, limit)

    @app.get("/api/documents/{document_id}")
    def document(document_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        return manager.document(document_id, offset, limit)

    @app.post("/api/search")
    def search(body: Search):
        return manager.search(body.question, body.limit)

    @app.get("/api/worksets/{workset_id}")
    def workset(
        workset_id: str,
        cursor: str | None = Query(None, max_length=4096),
        limit: int = Query(100, ge=1, le=1000),
    ):
        return manager.workset(workset_id, cursor, limit)

    @app.get("/api/investigations")
    def investigation_list():
        return investigations.list()

    @app.get("/api/investigation-graph")
    def investigation_workspace_graph():
        return investigations.workspace_graph()

    @app.post("/api/investigations")
    def investigation_create(body: Investigation):
        return investigations.create(**body.model_dump())

    @app.get("/api/investigations/{run_id}")
    def investigation_detail(run_id: str):
        return investigations.detail(run_id)

    @app.post("/api/investigations/{run_id}/cancel")
    def investigation_cancel(run_id: str):
        return investigations.cancel(run_id)

    @app.post("/api/investigations/{run_id}/annotations")
    def investigation_annotation(run_id: str, body: Annotation):
        return investigations.annotation(run_id, body.text)

    @app.post("/api/investigations/{run_id}/withdraw")
    def investigation_withdraw(run_id: str, body: Annotation):
        return investigations.withdraw(run_id, body.text)

    @app.post("/api/investigations/{run_id}/artifacts/{artifact_id}/revise")
    def investigation_revise(run_id: str, artifact_id: str, body: Revision):
        return investigations.revise(run_id, artifact_id, body.text)

    @app.get("/api/investigations/{run_id}/graph")
    def investigation_graph(run_id: str, mode: str = Query("supplied", pattern="^(supplied|cited)$")):
        return investigations.graph(run_id, mode)

    @app.get("/api/investigations/{run_id}/documents/{document_id}")
    def investigation_document(run_id: str, document_id: str):
        return investigations.document(run_id, document_id)

    @app.get("/api/investigations/{run_id}/artifacts/{artifact_id}")
    def investigation_artifact(run_id: str, artifact_id: str):
        with investigations.lock:
            investigations.require_readable(run_id)
            try:
                art = investigations.vault.artifact(artifact_id)
            except KeyError as exc:
                raise Missing("Unknown artifact") from exc
            if art["run_id"] != run_id:
                raise Missing("Artifact does not belong to this run")
            if art.get("deleted"):
                raise Missing("Artifact has been erased")
            # Always download untrusted source/agent bytes; never serve active HTML.
            data = investigations.vault.read_artifact(artifact_id)
            return Response(
                data,
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename*=UTF-8''"
                    + quote(art.get("name", artifact_id), safe=""),
                    "X-Content-Type-Options": "nosniff",
                },
            )

    @app.get("/api/investigations/{run_id}/artifacts/{artifact_id}/preview")
    def investigation_preview(run_id: str, artifact_id: str):
        with investigations.lock:
            investigations.require_readable(run_id)
            try:
                art = investigations.vault.artifact(artifact_id)
            except KeyError as exc:
                raise Missing("Unknown artifact") from exc
            if art["run_id"] != run_id:
                raise Missing("Artifact does not belong to this run")
            if art.get("deleted"):
                raise Missing("Artifact has been erased")
            data = investigations.vault.read_artifact(artifact_id)
            try:
                text = data[:100000].decode("utf-8")
            except UnicodeDecodeError:
                text = "Binary artifact: download the original file to inspect it."
            return {"text": text, "truncated": len(data) > 100000, "artifact": art}

    @app.post("/api/investigations/{run_id}/package")
    def investigation_package(run_id: str):
        # Finish and unlink the managed copy before releasing the erasure fence.
        # Once handed to HTTP, the response is an exported copy, like a download.
        with (
            investigations.lock,
            tempfile.TemporaryDirectory(prefix="graf-export-", dir=investigations.home) as directory,
        ):
            path = investigations.export(run_id, Path(directory) / "evidence.zip")
            data = path.read_bytes()
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="graf-{run_id}.zip"'},
        )

    @app.get("/api/integrity/checkpoint")
    def integrity_checkpoint():
        with investigations.lock:
            return investigations.vault.checkpoint()

    @app.post("/api/investigations/{run_id}/erasure-preview")
    def erasure_preview(run_id: str, body: Erasure):
        return investigations.erasure_preview(run_id, body.artifact_ids, body.reason)

    @app.post("/api/investigations/{run_id}/erase")
    def erase(run_id: str, body: Erasure):
        return investigations.erase(run_id, **body.model_dump())

    @app.get("/{path:path}")
    def ui(path: str):
        if path == "api" or path.startswith("api/"):
            raise Missing("Unknown API route")
        try:
            target = absolute(config.ui_dist / path)
            if not target.is_relative_to(config.ui_dist):
                raise ValueError("Outside static directory")
            if target.is_file():
                return FileResponse(target)
            if "." not in path.rsplit("/", 1)[-1]:
                index = absolute(config.ui_dist / "index.html")
                if index.is_file():
                    return FileResponse(index)
        except (ValueError, OSError):
            pass
        raise Missing("UI asset not found; build the dashboard before starting the server")

    return app
