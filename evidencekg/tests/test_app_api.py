"""Loopback authentication, CSRF, source fencing and static traversal tests."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_app_lifecycle import app_database, manager, ready

from evidencekg.app.api import COOKIE, create_app
from evidencekg.app.config import AppConfig

__all__ = ["app_database", "manager"]


@pytest.fixture
def client(manager):
    app = create_app(manager.config, manager=manager, start_background=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client


def auth():
    return {"Authorization": "Bearer " + "t" * 48}


def test_public_health_and_protected_endpoints(client):
    assert client.get("/healthz").json() == {"alive": True}
    assert client.get("/readyz").status_code == 503
    for route in ("status", "sources", "jobs", "graph", "documents", "settings", "worksets/old"):
        assert client.get("/api/" + route).status_code == 401
    assert client.post("/api/search", json={"question": "x"}).status_code == 401
    assert client.get("/api/status", headers=auth()).json()["state"] == "empty"
    assert client.get("/api/settings", headers=auth()).json().get("database_config") is None


def test_bootstrap_cookie_csrf_logout_and_bearer(client):
    payload = {"token": "t" * 48}
    assert client.post("/api/session", json=payload).status_code == 403
    assert (
        client.post("/api/session", json=payload, headers={"Origin": "http://evil.example"}).status_code
        == 403
    )
    origin = {"Origin": "http://127.0.0.1:8765"}
    bad = client.post("/api/session", json={"token": "x" * 48}, headers=origin)
    assert bad.status_code == 401
    response = client.post("/api/session", json=payload, headers=origin)
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert "t" * 48 not in cookie
    old_cookie = client.cookies.get(COOKIE)
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/rebuild").status_code == 403
    assert client.post("/api/rebuild", headers=origin).status_code == 200
    assert client.post("/api/logout", headers=origin).status_code == 200
    client.cookies.set(COOKIE, old_cookie)
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/rebuild", headers=auth()).status_code == 200


def test_host_origin_and_fetch_site_boundary(client):
    for host in ("evil.example", "127.0.0.1.evil.example:8765", "127.0.0.1:9999", "localhost", "[::1]:8765"):
        assert client.get("/healthz", headers={"Host": host}).status_code == 400
    assert client.get("/api/status", headers=auth() | {"Origin": "null"}).status_code == 403
    assert client.get("/api/status", headers=auth() | {"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/status", headers=auth() | {"Host": "localhost:8765"}).status_code == 200
    assert client.options("/api/status", headers={"Origin": "http://evil.example"}).status_code == 403
    assert "access-control-allow-origin" not in client.get("/healthz").headers


def test_input_validation_and_body_limits(client, manager):
    assert (
        client.post("/api/search", json={"question": "x", "limit": True}, headers=auth()).status_code == 422
    )
    assert client.post("/api/search", json={"question": "x" * 40000}, headers=auth()).status_code == 413
    assert client.post("/api/sources", json={"path": "/etc/passwd"}, headers=auth()).status_code == 422
    response = client.post(
        "/api/sources", json={"path": str(manager.config.allowed_roots[0])}, headers=auth()
    )
    assert response.status_code == 200
    sid = response.json()["id"]
    assert client.patch("/api/sources/" + sid, json={"enabled": "false"}, headers=auth()).status_code == 422
    assert client.get("/api/graph?limit=1000000", headers=auth()).status_code == 422


def test_real_lifecycle_http_views_and_old_workset_gate(client, manager):
    source = ready(manager)
    assert client.get("/readyz").status_code == 200
    response = client.post("/api/search", json={"question": "invoice"}, headers=auth())
    assert response.status_code == 200
    key = response.json()["workset_id"]
    doc = client.get("/api/documents", headers=auth()).json()["items"][0]["id"]
    assert client.get("/api/worksets/" + key, headers=auth()).status_code == 200
    assert client.delete("/api/sources/" + source["id"], headers=auth()).status_code == 200
    for route in ("graph", "documents", "documents/" + doc, "worksets/" + key):
        response = client.get("/api/" + route, headers=auth())
        assert response.status_code == 409
        assert "invoice" not in response.text
    assert client.get("/readyz").status_code == 503


def test_static_spa_symlink_traversal_and_security_headers(client, manager):
    assert "Real UI fixture" in client.get("/sources").text
    assert client.get("/missing.js").status_code == 404
    assert client.get("/api/unknown", headers=auth()).status_code == 404
    (manager.config.ui_dist / "leak.txt").symlink_to(manager.config.token_file)
    for route in ("/leak.txt", "/%2e%2e/token", "/%2fetc/passwd"):
        response = client.get(route)
        assert "t" * 48 not in response.text
        assert response.status_code == 404
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    policy = response.headers["content-security-policy"]
    assert "'wasm-unsafe-eval'" in policy
    assert "'unsafe-eval'" not in policy


def test_config_rejects_nonloopback_insecure_token_and_overlap(manager):
    with pytest.raises(ValueError, match="127.0.0.1"):
        replace(manager.config, host="0.0.0.0")
    with pytest.raises(ValueError, match="overlap"):
        replace(manager.config, allowed_roots=(manager.config.home.parent,))
    manager.config.token_file.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        create_app(manager.config, manager=manager, start_background=False)
    with pytest.raises(ValueError, match="absolute"):
        AppConfig.load("relative.json")
