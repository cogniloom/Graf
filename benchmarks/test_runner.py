import json

import pytest

from benchmarks.runner import (
    ACTION,
    Discovery,
    FileTools,
    digest,
    execute_case,
    inventory,
    prompt_for,
    report,
    usage_from_events,
    validate_citations,
    write,
)


def action(kind="answer", **overrides):
    return {
        "action": kind,
        "query": "",
        "path": "",
        "offset": 0,
        "answer": "",
        "abstain": False,
        "citations": [],
        **overrides,
    }


def event(**overrides):
    return {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 30,
            "output_tokens": 20,
            **overrides,
        },
    }


@pytest.mark.parametrize(
    "events",
    [
        [],
        [event(), event()],
        [event(input_tokens=-1)],
        [event(cached_input_tokens=101)],
        [event(output_tokens=True)],
        [event(), {"type": "turn.failed"}],
        [event(), {"item": {"type": "mcp_tool_call"}}],
    ],
)
def test_reject_incomplete_or_contaminated_usage(events):
    with pytest.raises(ValueError):
        usage_from_events(events)


def test_cached_tokens_not_double_counted():
    assert usage_from_events([event()]) == {
        "input_tokens": 100,
        "cached_input_tokens": 30,
        "output_tokens": 20,
    }


def test_file_boundaries_and_pagination(tmp_path):
    (tmp_path / "a.txt").write_text("needle\n" * 25)
    files = FileTools(tmp_path)
    first = files.search("NEEDLE", 0)
    assert len(first["hits"]) == 20 and first["next_offset"] == 20
    assert len(files.search("needle", 20)["hits"]) == 5
    assert "error" in files.read("../secret", 0)
    assert "error" in files.read("a.txt", -1)
    assert (
        validate_citations(
            action(
                citations=[
                    {"path": "a.txt", "quote": "needle"},
                    {"path": "a.txt", "quote": "invented"},
                ]
            ),
            files,
        )["citation_validity"]
        == 0.5
    )


def test_symlink_rejected(tmp_path):
    (tmp_path / "source").symlink_to(__file__)
    with pytest.raises(ValueError):
        inventory(tmp_path)


def test_baseline_cannot_use_discovery_or_gold():
    prompt = prompt_for("Question", "files", [], 2)
    assert "discover:" not in prompt and "expected_answer" not in prompt
    assert "discover:" in prompt_for("Question", "graf", [], 2)


class FakeWorker:
    def __init__(self, actions):
        self.actions = iter(actions)

    def call(self, prompt, folder):
        value = next(self.actions)
        if isinstance(value, Exception):
            raise value
        folder.mkdir()
        (folder / "prompt.txt").write_text(prompt)
        write(folder / "intent.json", {"test_fixture": True})
        write(folder / "schema.json", ACTION)
        write(folder / "answer.json", value)
        (folder / "events.jsonl").write_text(json.dumps(event()) + "\n")
        write(
            folder / "receipt.json",
            {
                "seconds": 0.01,
                "usage": usage_from_events([event()]),
                **{
                    key: digest((folder / name).read_bytes())
                    for name, key in (
                        ("prompt.txt", "prompt_sha256"),
                        ("intent.json", "intent_sha256"),
                        ("schema.json", "schema_sha256"),
                        ("answer.json", "answer_sha256"),
                        ("events.jsonl", "events_sha256"),
                    )
                },
            },
        )
        return value, usage_from_events([event()]), 0.01


def test_unknown_call_failure_keeps_partial_usage(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    (root / "a.txt").write_text("needle")
    worker = FakeWorker([action("search", query="needle"), ValueError("quota")])
    result = execute_case(
        {"id": "x", "category": "test", "question": "needle"},
        "files",
        FileTools(root),
        None,
        worker,
        tmp_path / "trial",
        3,
    )
    assert result["status"] == "failed"
    assert result["usage"]["input_tokens"] == 100
    assert not result["usage_complete"]


def test_graf_context_is_delivered_before_first_model_call(tmp_path):
    class InitialDiscovery:
        def search(self, question):
            assert question == "approval"
            return {"passages": [{"path": "a.txt", "text": "Approval is conditional."}]}

    root = tmp_path / "sources"
    root.mkdir()
    (root / "a.txt").write_text("Approval is conditional.")
    trial = tmp_path / "trial"
    result = execute_case(
        {"id": "x", "category": "test", "question": "approval"},
        "graf",
        FileTools(root),
        InitialDiscovery(),
        FakeWorker([action(answer="Conditional")]),
        trial,
        2,
    )
    assert result["status"] == "answered" and result["calls"] == 1
    assert "Approval is conditional." in (trial / "call-00/prompt.txt").read_text()
    assert (trial / "tool-initial.json").is_file()
    assert result["tool_seconds"] > 0


def test_real_graph_engine(tmp_path):
    from evidencekg.config import initialize
    from evidencekg.ingest import ingest

    root = tmp_path / "sources"
    root.mkdir()
    (root / "seed.txt").write_text("violet\nSee [[linked.txt]]")
    (root / "linked.txt").write_text("Approval is conditional.")
    store = initialize(tmp_path / "index", root)
    snapshot = ingest(store)
    manifest_sha = store.snapshot(snapshot)["manifest_sha"]
    store.close()
    write(
        tmp_path / "manifest.json",
        {"snapshot": snapshot, "snapshot_manifest_sha256": manifest_sha},
    )
    discovery = Discovery(tmp_path, "core")
    try:
        result = discovery.search("violet")
        assert {s["path"] for s in result["passages"]} == {"seed.txt", "linked.txt"}
    finally:
        discovery.close()


def test_report_counts_scheduled_missing_and_no_fake_accuracy(tmp_path, monkeypatch):
    monkeypatch.setattr("benchmarks.runner.verify", lambda dataset: None)
    write(tmp_path / "manifest.json", {"test_fixture": True})
    write(
        tmp_path / "run.json",
        {
            "dataset": str(tmp_path),
            "manifest_sha256": digest((tmp_path / "manifest.json").read_bytes()),
            "schedule": [
                {"case_id": "x", "repeat": 0, "arm": a} for a in ("files", "graf")
            ],
            "backend": "core",
            "corpus": {
                "file_count": 1,
                "source_lines": 2,
                "source_bytes": 3,
                "source_class": "test",
            },
            "ingestion_seconds": 1,
            "discovery_setup_seconds": 0.1,
            "file_setup_seconds": 0.01,
        },
    )
    trial = tmp_path / "trial-0000"
    root = tmp_path / "sources"
    root.mkdir()
    (root / "a.txt").write_text("needle")
    execute_case(
        {"id": "x", "question": "needle", "category": "test"},
        "files",
        FileTools(root),
        None,
        FakeWorker([action(citations=[{"path": "a.txt", "quote": "needle"}])]),
        trial,
        2,
        run_sha256=digest((tmp_path / "run.json").read_bytes()),
    )
    result = report(tmp_path)
    assert result["arms"]["graf"]["scheduled"] == 1
    assert result["arms"]["graf"]["recorded"] == 0
    assert result["arms"]["files"]["answer_accuracy"] is None
    assert result["paired_question_count"] == 0 and not result["publication_ready"]
    original = (trial / "call-00" / "events.jsonl").read_text()
    (trial / "call-00" / "events.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="artifact drift"):
        report(tmp_path)
    (trial / "call-00" / "events.jsonl").write_text(original)
    (trial / "result.json").rename(tmp_path / "saved-result.json")
    other = tmp_path / "trial-0001"
    other.mkdir()
    (tmp_path / "saved-result.json").rename(other / "result.json")
    with pytest.raises(ValueError, match="identity mismatch"):
        report(tmp_path)
    (other / "result.json").rename(trial / "result.json")
    grading = tmp_path / "grading"
    grading.mkdir()
    write(
        grading / "score-0000.json",
        {"trial": "trial-0000", "result_sha256": digest(b"wrong"), "pass": True},
    )
    with pytest.raises(ValueError, match="drift"):
        report(tmp_path)


def test_exclusive_receipt(tmp_path):
    path = tmp_path / "receipt.json"
    write(path, {"a": 1})
    with pytest.raises(FileExistsError):
        write(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 1}
