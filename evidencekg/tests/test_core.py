import pytest

from evidencekg.config import initialize
from evidencekg.db import Store
from evidencekg.ingest import ingest
from evidencekg.reports import backup, restore, verify
from evidencekg.retrieval import API
from evidencekg.review_queue import Queue


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    store = initialize(
        tmp_path / "state",
        root,
        {
            "identifiers": [{"namespace": "authority-X", "pattern": r"Case: (?P<value>\d+)"}],
            "names": ["Alex Smith"],
        },
    )
    yield root, store
    store.close()


def empty_result(task):
    return dict(
        task_id=task["task_id"],
        input_sha=task["input_sha"],
        findings=[],
        interpretations=[],
        unresolved_questions=[],
        uncertainty="",
        modality_reviewed=False,
    )


def drain(queue, run, edit=None):
    count = 0
    while (task := queue.next_review_task(run, "fake-v1")) is not None:
        result = empty_result(task)
        if edit:
            edit(task, result)
        queue.submit_review_result(run, task["task_id"], task["lease_id"], task["input_sha"], result)
        count += 1
    return count


def test_versions_gaps_partition_literal_and_graph(vault):
    root, store = vault
    (root / "a.txt").write_text("Case: 123\n\nAlex Smith did not agree on 2026-09-25.\n\nOnly")
    (root / "b.md").write_text("Case: 123\n\nAlex Smith did agree on 2026-09-25.")
    (root / "unsupported.xyz").write_bytes(b"\x00binary")
    (root / "empty.txt").write_bytes(b"")
    snapshot = ingest(store)
    api = API(store)
    docs = {d["path"]: d for d in api.inventory(snapshot)["items"]}
    assert docs["unsupported.xyz"]["status"] == "unsupported"
    assert docs["empty.txt"]["empty"]
    assert api.search(snapshot, "Only", "literal")["total"] == 1
    assert api.search(snapshot, "not", "lexical")["total"] == 1
    graph = api.explain_connection(
        snapshot, docs["a.txt"]["document_version_id"], docs["b.md"]["document_version_id"]
    )["items"]
    assert any(
        r["relationship"] == "SHARED_IDENTIFIER" and r["feature"]["namespace"] == "authority-X" for r in graph
    )
    assert any(r["relationship"] == "SHARED_NAME_SURFACE" for r in graph)
    assert not any(
        "did" in r.get("feature", {}).get("value", "") and r["relationship"] == "SHARED_EXACT_PASSAGE"
        for r in graph
    )
    (root / "a.txt").write_text("changed")
    changed = ingest(store)
    assert changed != snapshot
    assert api.search(snapshot, "Only", "literal")["total"] == 1
    assert api.search(changed, "Only", "literal")["total"] == 0
    assert verify(store)["ok"]


def test_queue_validation_leases_idempotency_and_singleton(vault):
    root, store = vault
    (root / "x.txt").write_text("Only")
    snapshot = ingest(store)
    queue = Queue(store)
    run = queue.start_review(snapshot, "Review", model="fake-v1")["run_id"]
    task = queue.next_review_task(run, "fake-v1")
    result = empty_result(task)
    result["task_id"] = "fabricated"
    with pytest.raises(ValueError):
        queue.submit_review_result(run, task["task_id"], task["lease_id"], task["input_sha"], result)
    assert queue.review_status(run)["result_validated"] == 0
    task = queue.next_review_task(run, "fake-v1")
    assert any(s["text"] == "Only" for s in task["payload"]["segments"])
    result = empty_result(task)
    assert queue.submit_review_result(run, task["task_id"], task["lease_id"], task["input_sha"], result)[
        "accepted"
    ]
    assert queue.submit_review_result(run, task["task_id"], task["lease_id"], task["input_sha"], result)[
        "idempotent"
    ]
    drain(queue, run)
    status = queue.review_status(run)
    assert status["scheduled"] == status["result_validated"]
    assert status["state"] == "scheduled_work_complete"
    assert verify(store)["ok"]


def test_fabricated_quote_rejected_and_proposal_attributed(vault):
    root, store = vault
    (root / "x.txt").write_text("Original rule")
    snapshot = ingest(store)
    queue = Queue(store)
    run = queue.start_review(snapshot, "Review")["run_id"]
    task = queue.next_review_task(run, "fake")
    seg = task["payload"]["segments"][0]
    ref = dict(segment_id=seg["id"], extraction_id=seg["extraction_id"], start=0, end=8, quote="Original")
    result = empty_result(task)
    result["findings"] = [
        dict(
            assertion="Observed text",
            epistemic_status="observation",
            sources=[{**ref, "quote": "tampered"}],
            uncertainty="",
        )
    ]
    with pytest.raises(ValueError, match="quotation"):
        queue.submit_review_result(run, task["task_id"], task["lease_id"], task["input_sha"], result)
    queue.store_interpretation(run, [ref], "POSSIBLE_RULE", "Model interpretation", "test-model")
    assert store.one("SELECT * FROM proposals")["attribution"] == "test-model"
    assert verify(store)["ok"]


def test_email_order_ambiguity_attachments(vault):
    from email.message import EmailMessage

    root, store = vault
    reply = EmailMessage()
    reply["Message-ID"] = "<reply@example.org>"
    reply["In-Reply-To"] = "<later@example.org>"
    reply.set_content("Reply body")
    nested = EmailMessage()
    nested.set_content("Nested text")
    nested.add_attachment(b"\x00unknown", maintype="application", subtype="octet-stream", filename="unknown.xyz")
    reply.add_attachment(nested)
    (root / "reply.eml").write_bytes(reply.as_bytes())
    first = ingest(store)
    assert (
        store.one("SELECT * FROM explicit_links WHERE relation_type='EMAIL_REPLY_REFERENCE'")["status"]
        == "unresolved"
    )
    target = EmailMessage()
    target["Message-ID"] = "<later@example.org>"
    target.set_content("Target")
    (root / "target.eml").write_bytes(target.as_bytes())
    second = ingest(store)
    links = store.rows(
        "SELECT l.* FROM explicit_links l JOIN snapshot_links sl ON sl.link_id=l.id WHERE sl.snapshot_id=? AND l.relation_type='EMAIL_REPLY_REFERENCE'",
        (second,),
    )
    assert {r["status"] for r in links} == {"resolved"}
    (root / "duplicate.eml").write_bytes(target.as_bytes())
    third = ingest(store)
    links = store.rows(
        "SELECT l.* FROM explicit_links l JOIN snapshot_links sl ON sl.link_id=l.id WHERE sl.snapshot_id=? AND l.relation_type='EMAIL_REPLY_REFERENCE'",
        (third,),
    )
    assert {r["status"] for r in links} == {"ambiguous"}
    docs = store.manifest(first)["documents"]
    assert len(docs) == 3
    assert any(d["status"] == "unsupported" and d["parent"] for d in docs)
    assert verify(store)["ok"]


def test_backup(vault, tmp_path):
    root, store = vault
    (root / "x.txt").write_text("Original")
    snap = ingest(store)
    checked = backup(store, tmp_path / "backup")
    assert checked["ok"]
    assert restore(tmp_path / "backup", tmp_path / "restored")["ok"]
    restored = Store(tmp_path / "restored")
    assert restored.snapshot()["id"] == snap
    restored.close()


def test_cursor_and_scope(vault):
    root, store = vault
    for i in range(13):
        (root / f"{i}.txt").write_text("same")
    snapshot = ingest(store)
    api = API(store)
    ids, cursor = [], None
    for size in [2, 3, 7, 1]:
        page = api.inventory(snapshot, cursor, size)
        ids.extend(d["document_version_id"] for d in page["items"])
        cursor = page["next_cursor"]
    assert len(set(ids)) == 13 and cursor is None
    cursor = api.inventory(snapshot, limit=1)["next_cursor"]
    (root / "new.txt").write_text("new")
    newer = ingest(store)
    with pytest.raises(ValueError):
        api.inventory(newer, cursor)
    with pytest.raises(ValueError):
        api.read_original_region(snapshot, "outside", {})
