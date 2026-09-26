"""Authenticated same-origin, loopback-only HTTP interface for one workspace."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .config import AppConfig, absolute
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


def create_app(config, *, manager=None, start_background=True):
    config = config if isinstance(config, AppConfig) else AppConfig.load(config)
    token = config.token()
    manager = manager or Manager(config)
    sessions = {}
    session_lock = threading.Lock()
    hosts = {f"127.0.0.1:{config.port}", f"localhost:{config.port}"}

    @asynccontextmanager
    async def lifespan(app):
        if start_background:
            manager.start()
        try:
            yield
        finally:
            if start_background:
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

    @app.get("/api/settings")
    def settings():
        return {
            "workspace_name": config.workspace_name or config.home.name,
            "codex_instructions": "Run ./graf plugin-install, then ask Codex to use Graf.",
            "allowed_roots": [str(p) for p in config.allowed_roots],
            "device": config.device,
            "host": config.host,
            "port": config.port,
            "scan_interval_seconds": config.scan_interval_seconds,
            "generative_model_calls": 0,
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
