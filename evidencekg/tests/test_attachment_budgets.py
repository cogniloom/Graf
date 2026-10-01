import io
import zipfile
from email.message import EmailMessage

import pytest

from evidencekg.config import configure
from evidencekg.ingest import ingest


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zipped:
        for name, data in entries:
            zipped.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize("limits", [{"max_attachments": 1}, {"max_attachment_bytes": 6}])
def test_exhausted_original_does_not_starve_later_emails(vault, limits):
    root, store = vault
    configure(store, limits)
    for name in ("a.eml", "b.eml"):
        msg = EmailMessage()
        msg.set_content("Message")
        for i in range(2):
            msg.add_attachment(b"1234", maintype="text", subtype="plain", filename=f"{i}.txt")
        (root / name).write_bytes(msg.as_bytes())
    for _ in range(2):
        docs = store.manifest(ingest(store))["documents"]
        for name in ("a.eml", "b.eml"):
            children = [d for d in docs if d["path"].startswith(name + "::")]
            assert len(children) == 2
            assert sorted(d["status"] for d in children) == ["failed", "ready"]
            failed = next(d for d in children if d["status"] == "failed")
            assert "Attachment aggregate limit reached" in failed["warnings"]
            # The parser itself omits bytes beyond its count limit; the
            # ingestion byte-budget gap retains bytes already acquired.
            assert (failed["blob"] is not None) == ("max_attachment_bytes" in limits)
            if "max_attachments" in limits:
                assert any("Attachment count limit reached" in w for w in failed["warnings"])


@pytest.mark.parametrize("budget", ["count", "bytes"])
def test_nested_containers_share_original_budget(vault, budget):
    root, store = vault
    nested = archive([("a.txt", b"1234"), ("b.txt", b"5678")])
    configure(
        store, {"max_attachments": 2} if budget == "count" else {"max_attachment_bytes": len(nested) + 4}
    )
    (root / "outer.zip").write_bytes(archive([("inner.zip", nested)]))
    for _ in range(2):
        docs = store.manifest(ingest(store))["documents"]
        leaf = next(d for d in docs if d["path"].endswith("/b.txt"))
        assert leaf["status"] == "failed"
        assert "Attachment aggregate limit reached" in leaf["warnings"]
        assert next(d for d in docs if d["path"].endswith("/a.txt"))["status"] == "ready"
