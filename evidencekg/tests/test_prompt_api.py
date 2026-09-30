"""Real authenticated HTTP boundaries and durable storage, with fixture retrieval."""

import base64
from uuid import uuid4

from fastapi.testclient import TestClient
from test_investigation_service import Answerer, Sources

from evidencekg.app import AppConfig
from evidencekg.app.api import create_app
from evidencekg.investigations.service import Investigations


def test_accepted_upload_does_not_wait_for_retrieval_and_stays_authenticated(tmp_path):
    sources = Sources(tmp_path / "home")
    token = tmp_path / "token"
    token.write_text("a" * 40)
    token.chmod(0o600)
    config = AppConfig(
        home=sources.config.home,
        database_config=tmp_path / "db.json",
        models=tmp_path / "models",
        token_file=token,
        ui_dist=tmp_path / "ui",
    )
    sources.config = config
    service = Investigations(sources, Answerer())
    app = create_app(config, manager=sources, investigations=service, start_background=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        auth = {"Authorization": "Bearer " + config.token()}
        payload = {
            "prompt": "Read memo.txt",
            "request_id": uuid4().hex,
            "include_collection": False,
            "attachments": [{"name": "memo.txt", "data": base64.b64encode(b"X" * 40000).decode()}],
        }
        assert client.post("/api/investigations", json=payload).status_code == 401
        response = client.post("/api/investigations", json=payload, headers=auth)
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["state"] == "preparing"
        assert client.post("/api/investigations", json=payload, headers=auth).json()["id"] == run["id"]
        service._prepare(run["id"])
        assert service.detail(run["id"])["coverage"]["supplied_passages"] == 1
        assert (
            client.post(
                "/api/investigations", json=payload, headers=auth | {"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        invalid = {
            **payload,
            "request_id": uuid4().hex,
            "attachments": [{"name": "../bad.txt", "data": "WA=="}],
        }
        assert client.post("/api/investigations", json=invalid, headers=auth).status_code == 422
        assert client.post("/api/search", json={"question": "X" * 40000}, headers=auth).status_code == 413
        assert client.get("/api/investigations/" + run["id"], headers=auth).status_code == 200
    service.stop()
