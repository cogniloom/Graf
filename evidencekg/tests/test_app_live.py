"""Opt-in real native FastAPI + local non-generative model smoke, synthetic inputs.

Requires EVIDENCEKG_APP_TEST_ADMIN_CONFIG, EVIDENCEKG_APP_TEST_PYTHON and
EVIDENCEKG_APP_TEST_MODELS. No downloads, private corpus, or benchmark grading.
"""

import json
import os
import socket
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from test_app_lifecycle import app_database, manager

__all__ = ["app_database", "manager"]


def test_native_server_real_local_models_and_removal(manager, tmp_path):
    python = os.environ.get("EVIDENCEKG_APP_TEST_PYTHON")
    models = os.environ.get("EVIDENCEKG_APP_TEST_MODELS")
    if not python or not models:
        pytest.skip("Set explicit native Python and existing local model paths for live smoke")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    config = asdict(manager.config)
    config.update(models=models, port=port, device="cpu", scan_interval_seconds=0.2)
    config["allowed_roots"] = [str(p) for p in config["allowed_roots"]]
    config = {k: str(v) if isinstance(v, Path) else v for k, v in config.items()}
    path = tmp_path / "app.json"
    path.write_text(json.dumps(config))
    log = tmp_path / "native.log"
    env = os.environ | {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")
        + os.pathsep
        + os.environ.get("PYTHONPATH", ""),
    }
    with log.open("w") as output:
        process = subprocess.Popen(
            [python, "-m", "evidencekg.app", "--config", str(path)],
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert process.poll() is None, "Native server exited; inspect isolated native.log"
                    time.sleep(0.1)
                else:
                    pytest.fail("Native server failed to start")
                auth = {"Authorization": "Bearer " + manager.config.token()}
                assert client.get("/api/status").status_code == 401
                item = client.post(
                    "/api/sources", json={"path": str(manager.config.allowed_roots[0])}, headers=auth
                )
                assert item.status_code == 200
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    status = client.get("/api/status", headers=auth).json()
                    if status["state"] in ("ready", "ready_with_gaps"):
                        break
                    assert status["state"] != "blocked", status["message"]
                    time.sleep(0.25)
                else:
                    pytest.fail("Actual local model preparation timed out")
                assert status["models_ready"]
                assert status["counts"]["documents"] == 1
                response = client.post(
                    "/api/search", json={"question": "Was the invoice approved?", "limit": 1}, headers=auth
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["backend"] == "local-hybrid-ladybugdb"
                assert result["generative_model_calls"] == 0
                assert any("not approved" in s["text"] for s in result["source_context"].values())
                workset = result["workset_id"]
                assert client.get("/api/worksets/" + workset, headers=auth).status_code == 200
                assert client.delete("/api/sources/" + item.json()["id"], headers=auth).status_code == 200
                assert client.get("/api/worksets/" + workset, headers=auth).status_code == 409
                assert client.get("/api/documents", headers=auth).status_code == 409
                assert client.get("/readyz").status_code == 503
                assert (manager.config.allowed_roots[0] / "a.txt").read_text().endswith("not approved.\n")
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
