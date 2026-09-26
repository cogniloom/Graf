"""Synthetic mechanics, independent of private benchmark labels and live inference."""

import sqlite3
from types import SimpleNamespace

import pytest
from evidencekg.config import initialize
from evidencekg.db import sha
from evidencekg.ingest import ingest

from poc_accuracy.retrieval import HybridDiscovery
from poc_accuracy.sources import ReadOnlyStore, load_sources
from poc_accuracy.workset import Workset


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    store = initialize(tmp_path / "vault", root, {})
    yield root, store
    store.close()


def test_workset_resume_and_foreign_cursor(tmp_path):
    path = tmp_path / "workset.sqlite"
    rows = [{"segment_id": str(i), "routes": ["literal"]} for i in range(7)]
    ws = Workset(path)
    key = ws.freeze({"snapshot": "a", "question": "q"}, rows, {"gaps": []})
    page = ws.page(key, limit=2)
    cursor = page["next_cursor"]
    ws.mark_reviewed(key, "0", {"source": "human review receipt"})
    ws.close()
    ws = Workset(path)
    assert ws.freeze({"snapshot": "a", "question": "q"}, rows, {"gaps": []}) == key
    seen = [r["segment_id"] for r in page["items"]]
    while cursor:
        page = ws.page(key, cursor=cursor, limit=3)
        seen.extend(r["segment_id"] for r in page["items"])
        cursor = page["next_cursor"]
    assert seen == [str(i) for i in range(7)]
    assert ws.page(key)["items"][0]["review_state"] == "reviewed"
    key2 = ws.freeze({"snapshot": "b", "question": "q"}, rows, {})
    with pytest.raises(ValueError, match="cursor"):
        ws.page(key2, cursor=ws.page(key, limit=1)["next_cursor"])
    with pytest.raises(ValueError, match="drift"):
        ws.freeze({"snapshot": "a", "question": "q"}, rows[:-1], {"gaps": []})
    ws.close()


def test_readonly_and_source_drift(vault):
    root, store = vault
    (root / "a.txt").write_text("An original immutable detail.")
    snapshot = ingest(store)
    ro = ReadOnlyStore(store.state)
    _, segments, _ = load_sources(ro, snapshot)
    assert len(segments) == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.db.execute("DELETE FROM segments")
    with pytest.raises(ValueError, match="Read-only"):
        ro.put(b"must not be written")
    ro.close()
    sid = next(iter(segments))
    # Updating both row text and row hash still must fail against original artifact.
    store.db.execute("UPDATE segments SET text=?,text_sha=? WHERE id=?", ("tamper", sha("tamper"), sid))
    ro = ReadOnlyStore(store.state)
    with pytest.raises(ValueError, match="Immutable source"):
        load_sources(ro, snapshot)
    ro.close()


class Reranker:
    def score(self, question, passages):
        return SimpleNamespace(
            scores=[float("1847" in p) for p in passages],
            windows=[(SimpleNamespace(start=0, end=len(p)),) for p in passages],
        )


def test_union_bundle_ledger_and_originals(vault, tmp_path):
    from email.message import EmailMessage

    root, store = vault
    mail = EmailMessage()
    mail["Message-ID"] = "<order@example.org>"
    mail["To"] = "recipient@example.org"
    mail.set_content("Order 1847 requested.")
    reply = EmailMessage()
    reply["In-Reply-To"] = "<order@example.org>"
    reply.set_content("Approved.")
    (root / "mail.eml").write_bytes(mail.as_bytes())
    (root / "reply.eml").write_bytes(reply.as_bytes())
    (root / "semantic.txt").write_text("Eine Erlaubnis wurde erteilt.")
    (root / "gap.bin").write_bytes(b"\x00")
    snapshot = ingest(store)
    ro = ReadOnlyStore(store.state)
    _, segments, _ = load_sources(ro, snapshot)
    dense_ids = [sid for sid, s in segments.items() if "Erlaubnis" in s["text"]]
    ws = Workset(tmp_path / "worksets.sqlite")
    ranker = HybridDiscovery(
        ro,
        snapshot,
        lambda q: [(sid, 1.0) for sid in dense_ids],
        Reranker(),
        ws,
        model_identity={"toy": True},
    )
    result = ranker.retrieve("What about order 1847?")
    rows = ws.page(result["workset_id"], limit=1000)["items"]
    assert set(dense_ids) <= set(r["segment_id"] for r in rows)
    assert any("Approved" in segments[r["segment_id"]]["text"] for r in rows)
    accounting = result["candidate_accounting"]
    assert len(rows) + len(accounting["not_discovered"]) + len(accounting["empty_or_gap"]) == len(segments)
    assert all(s == segments[s["id"]] for s in result["segments"])
    assert any(s["locators"] for s in result["segments"])
    assert all(r["review_state"] == "pending" for r in rows)  # scoring is not semantic review
    ws.close()
    ro.close()


def test_workset_drift_and_symlink(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ws = Workset(path)
    key = ws.freeze({"q": "q"}, [{"segment_id": "a"}, {"segment_id": "b"}], {})
    other = sqlite3.connect(path)
    other.execute("DELETE FROM candidates WHERE sid=?", ("b",))
    other.commit()
    other.close()
    with pytest.raises(ValueError, match="inventory drift"):
        ws.page(key)
    ws.close()
    alias = tmp_path / "alias.sqlite"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="Symlink"):
        Workset(alias)
