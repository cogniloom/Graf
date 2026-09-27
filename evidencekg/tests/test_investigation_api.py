"""Authenticated investigation journey over real local storage and PostgreSQL."""

import json
from uuid import uuid4

from fastapi.testclient import TestClient
from test_app_lifecycle import app_database, manager, ready

from evidencekg.app.api import create_app
from evidencekg.investigations.service import Investigations
from evidencekg.investigations.verifier import verify_package

__all__ = ["app_database", "manager"]


class SyntheticAdapter:
    def execute(self, prompt, cwd, on_event, cancel, **kwargs):
        segment = json.loads(prompt)["passages"][0]
        on_event({"type": "synthetic", "text": "Synthetic provider fixture"})
        return {
            "answer": "The invoice was not approved.",
            "citations": [{"segment_id": segment["id"], "quote": "not approved"}],
            "documents": [
                {
                    "name": "report.html",
                    "media_type": "text/html",
                    "content": "<script>throw new Error('unsafe')</script>",
                }
            ],
        }


def test_authenticated_run_package_withdrawal_and_erasure(manager, tmp_path):
    ready(manager)
    service = Investigations(manager, SyntheticAdapter())
    app = create_app(manager.config, manager=manager, investigations=service, start_background=False)
    auth = {"Authorization": "Bearer " + manager.config.token()}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/investigations").status_code == 401
        response = client.post(
            "/api/investigations",
            headers=auth,
            json={"prompt": "Was approval given?", "request_id": uuid4().hex},
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["id"]
        service.execute(run_id)
        base = "/api/investigations/" + run_id
        detail = client.get(base, headers=auth).json()
        assert detail["state"] == "completed", detail
        assert detail["result"]["citations"][0]["valid"]
        output = next(a for a in detail["artifacts"] if a.get("name") == "report.html")
        download = client.get(base + "/artifacts/" + output["id"], headers=auth)
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert (
            client.get(base + "/artifacts/" + output["id"] + "/preview", headers=auth)
            .json()["text"]
            .startswith("<script>")
        )
        assert (
            client.get("/api/investigations/other/artifacts/" + output["id"], headers=auth).status_code == 404
        )
        package = client.post(base + "/package", headers=auth)
        assert package.status_code == 200, package.text
        path = tmp_path / "package.zip"
        path.write_bytes(package.content)
        assert verify_package(path)["valid"]
        assert (
            client.post(base + "/withdraw", headers=auth, json={"text": "Superseded analysis"}).status_code
            == 200
        )
        assert client.post(
            "/api/investigations",
            headers=auth,
            json={"prompt": "Continue", "parent_id": run_id, "request_id": uuid4().hex},
        ).status_code in (409, 422)
        body = {"artifact_ids": [output["id"]], "reason": "Synthetic deletion order"}
        preview = client.post(base + "/erasure-preview", headers=auth, json=body)
        assert preview.status_code == 200, preview.text
        assert client.post(base + "/erase", headers=auth, json=body).status_code == 422
        result = client.post(
            base + "/erase",
            headers=auth,
            json=body | {k: preview.json()[k] for k in ("preview_hash", "confirmation")},
        )
        assert result.status_code == 200, result.text
        assert client.get(base, headers=auth).json()["state"] == "erased"
        assert client.get(base + "/artifacts/" + output["id"], headers=auth).status_code in (404, 410, 422)
