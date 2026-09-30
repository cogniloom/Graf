"""Opt-in real PostgreSQL publication, HTTP authentication and source fences."""

import json

from test_app_api import app_database, auth, client, manager

from evidencekg.hybrid.postgres import _connect

__all__ = ["app_database", "client", "manager"]


def test_bilingual_knowledge_http_graph_details_and_source_revocation(client, manager):
    root = manager.config.allowed_roots[0]
    (root / "a.txt").write_text(
        "Order 1847 was approved on 12 March 2026.\n"
        "Die Bestellung 1847 wurde am 12. März 2026 nicht genehmigt."
    )
    assert client.get("/api/knowledge").status_code == 401
    source = manager.register(str(root))
    assert manager.run_once(), json.dumps(manager.jobs(), default=str, indent=2)
    response = client.get(
        "/api/knowledge", params={"kind": "evidence_set", "entity": "Bestellung:1847"}, headers=auth()
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 2
    assert {c["language"] for c in data["items"]} == {"en", "de"}
    assert all(c["source_path"] == str(root / "a.txt") for c in data["items"])
    graph = client.get("/api/investigation-graph", headers=auth()).json()
    node = next(n for n in graph["nodes"] if n["kind"] == "knowledge_claim")
    assert "knowledge_record" not in node  # graph payload is compact
    params = {k: node["details"][k] for k in ("kind", "group_id")}
    detail = client.get("/api/knowledge", params=params, headers=auth()).json()
    assert detail["items"][0]["id"] == node["id"]
    assert detail["items"][0]["sources"]
    assert detail["snapshot_id"] == node["details"]["snapshot_id"]
    for params in (
        {"kind": "invalid"},
        {"kind": "evidence_set"},
        {"limit": 0},
        {"kind": "claim", "applicable_on": "03/04/2026"},
    ):
        assert client.get("/api/knowledge", params=params, headers=auth()).status_code in {400, 422}
    assert client.delete("/api/sources/" + source["id"], headers=auth()).status_code == 200
    response = client.get("/api/knowledge", headers=auth())
    assert response.status_code == 409
    assert "genehmigt" not in response.text and "approved" not in response.text


def test_rules_upgrade_queues_once_after_publication_without_source_changes(manager):
    root = manager.config.allowed_roots[0]
    (root / "a.txt").write_text("Die Bestellung 1847 wurde genehmigt.")
    manager.register(str(root))
    assert manager.run_once(), manager.jobs()
    before = manager.status()["revision"]
    # Emulate a retained publication produced by an older code/model generation.
    with _connect(manager.dsn) as db:
        db.execute(
            "UPDATE public.docworm_workspace SET publication=publication-'knowledge_signature'-'knowledge_request_signature' WHERE id=%s",
            (manager.id,),
        )
    assert manager.reconcile()
    assert manager.status()["revision"] == before + 1
    assert manager.reconcile()
    assert manager.status()["revision"] == before + 1  # queued job is not repeatedly superseded
    assert manager.run_once(), manager.jobs()
    assert manager.reconcile()
    assert manager.status()["revision"] == before + 1
    assert manager.knowledge(entity="Bestellung:1847")["total"] == 1


def test_parent_worker_signature_skew_does_not_loop_rebuilds(manager, monkeypatch):
    from evidencekg import knowledge

    original = knowledge.signature()
    # A running parent can retain old modules while a fresh child reads updated
    # files. The requested generation and actual result must remain distinct.
    with monkeypatch.context() as patch:
        patch.setattr(knowledge, "signature", lambda: original | {"version": "loaded-parent-generation"})
        manager.register(str(manager.config.allowed_roots[0]))
        assert manager.run_once(), manager.jobs()
        revision = manager.status()["revision"]
        assert manager.status()["knowledge_restart_required"]
        for _ in range(3):
            assert manager.reconcile()
            assert manager.status()["revision"] == revision
    # Simulate the restarted parent now loading the same generation as children.
    assert manager.reconcile()
    assert manager.status()["revision"] == revision + 1
    assert manager.run_once(), manager.jobs()
    assert not manager.status()["knowledge_restart_required"]
    assert manager.reconcile()
    assert manager.status()["revision"] == revision + 1


def test_validity_identity_and_supersession_http_readbacks(client, manager):
    root = manager.config.allowed_roots[0]
    (root / "policy.txt").write_text(
        "Richtlinie P1 gilt ab 1. März 2026 bis einschließlich 31. März 2026.\n"
        "Policy P2 supersedes policy P1 from 1 April 2026.\n"
        "Name: Andreas Müller; E-Mail: a@example.test\n"
        "Name: Andreas Mueller; email: a@example.test"
    )
    manager.register(str(root))
    assert manager.run_once(), manager.jobs()
    response = client.get(
        "/api/knowledge",
        params={"kind": "claim", "entity": "Richtlinie:P1", "valid_at": "20 March 2026"},
        headers=auth(),
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    evidence = client.get(
        "/api/knowledge",
        params={"kind": "evidence_set", "entity": "policy:P1", "valid_at": "20. März 2026"},
        headers=auth(),
    ).json()
    assert evidence["total"] == 2
    assert {r["predicate"] for r in evidence["items"]} == {"validity", "supersession"}
    identity = client.get("/api/knowledge", params={"kind": "identity"}, headers=auth()).json()
    assert identity["total"] == 1
    assert all(
        r["source_path"] == str(root / "policy.txt") for r in identity["items"][0]["supporting_records"]
    )
