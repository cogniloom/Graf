from types import SimpleNamespace

import pytest
from test_app_lifecycle import app_database, manager

__all__ = ["app_database", "manager"]

from evidencekg.app.manager import Manager


def test_issue_filter_is_applied_before_pagination_and_preserves_reasons():
    documents = [
        dict(
            document_version_id=str(i),
            path=f"folder/file{i}.xyz",
            status="unsupported" if i % 2 else "ready",
            warnings=["Unsupported type"] if i % 2 else [],
            empty=False,
        )
        for i in range(120)
    ]
    manager = SimpleNamespace(
        evidence=lambda operation: operation({}),
        _snapshot=lambda row: ({"documents": documents}, {}, []),
        _path=lambda path, row: "/original/" + path,
    )
    result = Manager.documents(manager, offset=50, limit=50, issues="unsupported")
    assert result["total"] == 60
    assert len(result["items"]) == 10
    assert result["items"][0]["path"] == "/original/folder/file101.xyz"
    assert all(
        d["status"] == "unsupported" and d["warnings"] == ["Unsupported type"] for d in result["items"]
    )
    assert Manager.documents(manager)["total"] == 120
    documents[0].update(status="partial", warnings=["OCR incomplete"])
    assert Manager.documents(manager, issues="all")["total"] == 61
    assert Manager.documents(manager, issues="partial")["total"] == 1
    assert Manager.documents(manager, issues="failed")["total"] == 0
    documents[2].update(status="failed", warnings=["PDF parser failed: cannot open broken document"])
    failed = Manager.documents(manager, issues="failed")
    assert failed["total"] == 1
    assert failed["items"][0]["warnings"] == ["PDF parser failed: cannot open broken document"]
    with pytest.raises(ValueError):
        Manager.documents(manager, issues="invented")


def test_real_issue_endpoint_requires_auth_and_current_sources(manager):
    from fastapi.testclient import TestClient

    from evidencekg.app.api import create_app

    root = manager.config.allowed_roots[0]
    (root / "unsupported.xyz").write_text("Synthetic unsupported document")
    source = manager.register(str(root))
    assert manager.run_once()
    headers = {"Authorization": "Bearer " + "t" * 48}
    with TestClient(
        create_app(manager.config, manager=manager, start_background=False), base_url="http://127.0.0.1:8765"
    ) as client:
        assert client.get("/api/document-issues").status_code == 401
        result = client.get("/api/document-issues", headers=headers)
        assert result.status_code == 200
        body = result.json()
        assert body["total"] == 1
        assert body["items"][0]["path"] == str(root / "unsupported.xyz")
        assert body["items"][0]["warnings"] == ["Unsupported format: .xyz"]
        assert client.get("/api/document-issues?kind=invalid", headers=headers).status_code == 422
        manager.change(source["id"], remove=True)
        assert client.get("/api/document-issues", headers=headers).status_code == 409
