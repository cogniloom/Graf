import json

import pytest

from evidencekg.config import DEFAULTS
from evidencekg.evaluation import paired_summary, sample_corpus
from evidencekg.parsers import parse


def test_sample_seed_scope_and_failures(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for i in range(8):
        (root / f"{i}.txt").write_text(str(i))
    (root / "unsupported.xyz").write_bytes(b"binary")
    (root / "escape.txt").symlink_to(tmp_path / "absent")
    counts = {"text": 9, "unsupported": 1}
    a = sample_corpus(root, tmp_path / "a", counts=counts)
    b = sample_corpus(root, tmp_path / "b", counts=counts)
    assert a == b
    assert len(a["selected"]) == 10 and not a["inventory_complete"]
    assert sum(x["capture_status"] == "failed" for x in a["selected"]) == 1
    assert json.loads((tmp_path / "a/sample.json").read_text()) == a
    with pytest.raises(ValueError):
        sample_corpus(root, tmp_path / "a")


def test_paired_failures_stay_in_denominator():
    rows = [
        {"case_id": "q", "arm": "plain", "valid": False, "correct": False},
        {"case_id": "q", "arm": "graph", "valid": True, "correct": True},
    ]
    result = paired_summary(rows)
    assert result["arms"]["plain"]["denominator"] == 1
    assert result["arms"]["plain"]["missing_or_invalid"] == 1
    with pytest.raises(ValueError):
        paired_summary(rows + [{"case_id": "q2", "arm": "plain"}])


def test_malformed_email_header_preserves_body():
    raw = b"Subject: bad \xff\xfe\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nOnly if written approval exists."
    result = parse(raw, ".eml", DEFAULTS)
    assert result["status"] == "partial"
    assert any("Only if written approval exists." in s["text"] for s in result["sections"])
    assert any("escaped" in w for w in result["warnings"])
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_pdf_binary_annotation_not_prose(tmp_path):
    import io

    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, ByteStringObject, DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    annotation = DictionaryObject(
        {
            NameObject("/Contents"): TextStringObject("Approval is not granted."),
            NameObject("/V"): DictionaryObject({NameObject("/Contents"): ByteStringObject(b"\xff" * 200)}),
        }
    )
    page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
    out = io.BytesIO()
    writer.write(out)
    result = parse(out.getvalue(), ".pdf", {**DEFAULTS, "ocr": "off"})
    text = "\n".join(s["text"] for s in result["sections"])
    assert "Approval is not granted." in text and "\\xff" not in text
    assert any("annotation" in a["name"] for a in result["artifacts"])


def test_assurance_view_cursor_and_export(vault, tmp_path):
    from test_assurance import observation, result_for, setup, submit

    from evidencekg.reports import export, verify
    from evidencekg.review_views import review_evidence

    store, queue, rid = setup(vault)
    task = queue.next_review_task(rid, "fake", kinds=["source"])
    result = result_for(task)
    result["observations"] = [observation(task["payload"]["segments"][0])]
    submit(queue, task, result)
    first = review_evidence(store, rid, limit=1)
    assert first["next_cursor"]
    nxt = review_evidence(store, rid, first["next_cursor"], limit=100)
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in nxt["items"]})
    task = queue.next_review_task(rid, "fake", kinds=["source"])
    result = result_for(task)
    result["observations"] = [observation(task["payload"]["segments"][0])]
    submit(queue, task, result)
    with pytest.raises(ValueError, match="cursor"):
        review_evidence(store, rid, first["next_cursor"], limit=2)
    output = tmp_path / "report"
    export(store, rid, output)
    assert json.loads((output / "assurance.json").read_text())["status"]["structured_observations"] == 2
    assert verify(store)["ok"]


def test_cli_enhanced_default_is_explicitly_reversible():
    from evidencekg.cli import parser

    assert parser().parse_args(["review", "--question-file", "q.txt"]).enhanced
    assert not parser().parse_args(["review", "--question-file", "q.txt", "--no-enhanced"]).enhanced


@pytest.mark.parametrize("rule", ["mechanical-v1", "mechanical-v2"])
def test_reextraction_reference_citations_are_snapshot_bound(vault, rule):
    from evidencekg.config import configure
    from evidencekg.ingest import ingest
    from evidencekg.reports import verify
    from evidencekg.retrieval import API
    from evidencekg.review_queue import Queue

    root, store = vault
    configure(store, {"rules_version": rule})
    (root / "a.txt").write_text("Read [[b.txt]] for the exception.")
    (root / "b.txt").write_text("Only if approved.")
    first = ingest(store)
    api = API(store)
    a = next(d for d in store.manifest(first)["documents"] if d["path"] == "a.txt")
    b = next(d for d in store.manifest(first)["documents"] if d["path"] == "b.txt")
    before = api.explain_connection(first, a["document_version_id"], b["document_version_id"])
    second = ingest(store, reextract=True)
    assert first != second
    assert api.explain_connection(first, a["document_version_id"], b["document_version_id"]) == before
    q = Queue(store)
    rid = q.start_review(second, "Review")["run_id"]
    active = {d["extraction_id"] for d in store.manifest(second)["documents"]}
    for row in store.rows("SELECT input_manifest_sha FROM review_tasks WHERE run_id=?", (rid,)):
        packet = json.loads(store.get(row["input_manifest_sha"]))
        assert all(s["extraction_id"] in active for s in packet["segments"])
    assert verify(store)["ok"]


def test_benchmark_freezes_observations_during_concurrent_review(vault, tmp_path):
    from test_assurance import observation, ref, result_for, submit

    from evidencekg.benchmark import run_benchmark
    from evidencekg.ingest import ingest
    from evidencekg.retrieval import API
    from evidencekg.review_queue import Queue

    root, store = vault
    for i in range(8):
        (root / f"literal-{i}.txt").write_text(f"approval administrative note {i}")
    (root / "other.txt").write_text("Permission was granted Friday.")
    sid = ingest(store)
    api, q = API(store), Queue(store)
    rid = q.start_review(sid, "Review", enhanced=True)["run_id"]
    target = api.search(sid, "Permission")["items"][0]

    class ChangingWorker:
        identity, model, effort = "fake-frozen-observations", "fake", "none"

        def __init__(self):
            self.calls = []

        def call(self, stage, payload, schema):
            self.calls.append(payload)
            if len(self.calls) == 1:
                while task := q.next_review_task(rid, "fake", kinds=["source"]):
                    result = result_for(task)
                    if task["payload"]["segments"][0]["id"] == target["id"]:
                        obs = observation(task["payload"]["segments"][0])
                        obs.update(
                            issue_keys=[], event_keys=[], categories=[], semantic_query_terms=["approval"]
                        )
                        result["observations"] = [obs]
                    submit(q, task, result)
            return {"assertion": "Unknown", "sources": [], "uncertainty": "No proof"}

    worker = ChangingWorker()
    cases = [
        {
            "id": "q",
            "question": "When was approval?",
            "expected_answer": "Friday",
            "category": "synthetic",
            "evidence_refs": [ref(target)],
        }
    ]
    report = run_benchmark(store, cases, worker, tmp_path / "bench", enhanced_run_id=rid, limit=3)
    assert len(report["rows"]) == 3
    assert worker.calls[0]["segments"] == next(c for c in worker.calls if c["reasons"])["segments"]
    assert json.loads((tmp_path / "bench/observations.json").read_text()) == []


def test_benchmark_cannot_write_into_source_corpus(vault):
    from evidencekg.benchmark import run_benchmark

    root, store = vault
    with pytest.raises(ValueError, match="outside"):
        run_benchmark(store, [], None, root / "benchmark")
    assert not (root / "benchmark").exists()
