"""Synthetic receipt fixtures test accounting; they never establish model quality."""

import json
from pathlib import Path

import pytest

from benchmarks import runner
from benchmarks.analyze import analyze, export_review


class ReceiptWorker:
    version = "TEST FIXTURE — not live Codex"
    outputs = []

    def __init__(self, timeout):
        self.outputs = iter(type(self).outputs)

    def call(self, prompt, folder, schema=runner.ACTION):
        value = next(self.outputs)
        folder.mkdir()
        (folder / "prompt.txt").write_text(prompt)
        runner.write(folder / "intent.json", {"test_fixture": True})
        runner.write(folder / "schema.json", schema)
        runner.write(folder / "answer.json", value)
        usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10}
        (folder / "events.jsonl").write_text(
            json.dumps({"type": "turn.completed", "usage": usage}) + "\n"
        )
        runner.write(
            folder / "receipt.json",
            {
                "seconds": 0.01,
                "usage": usage,
                **{
                    key: runner.digest((folder / name).read_bytes())
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
        return value, usage, 0.01


def fixture_run(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset"
    runner.prepare(dataset, "documents", 30, Path(__file__).resolve().parents[1])
    case = json.loads((dataset / "cases.json").read_text())[0]
    good = {
        "action": "answer",
        "query": "",
        "path": "",
        "offset": 0,
        "answer": case["expected_answer"],
        "abstain": False,
        "citations": case["required_evidence"],
    }
    bad = {
        **good,
        "citations": [{"path": good["citations"][0]["path"], "quote": "FABRICATED"}],
    }
    ReceiptWorker.outputs = [bad, good]
    monkeypatch.setattr(runner, "Codex", ReceiptWorker)
    output = tmp_path / "run"
    runner.run(dataset, output, "core", 1, 2, 1, 5, None)
    return output


def test_positive_judge_cannot_override_invalid_citation(tmp_path, monkeypatch):
    output = fixture_run(tmp_path, monkeypatch)
    ReceiptWorker.outputs = [
        {
            "correct": True,
            "complete": True,
            "supported": True,
            "rationale": "Test fixture deliberately accepts everything",
        }
    ] * 2
    runner.grade(output, 5)
    scores = [
        json.loads(p.read_text()) for p in (output / "grading").glob("score-*.json")
    ]
    assert sum(s["pass"] for s in scores) == 1
    assert sum(s["citation_gate"] for s in scores) == 1
    result = analyze(output)
    assert result["complete_measured_and_graded"]
    assert result["both_strict_pass_pairs"] == 0
    assert result["paired"]["both_correct_seconds_saved"]["mean"] is None
    review = tmp_path / "review"
    export_review(output, review)
    packet = json.loads((review / "reviewer-packet.json").read_text())
    assert len(packet) == 2
    assert all("arm" not in row and "usage" not in row for row in packet)


def test_resealed_fabricated_usage_rejected_against_events(tmp_path, monkeypatch):
    output = fixture_run(tmp_path, monkeypatch)
    trial = output / "trial-0000"
    path = trial / "result.json"
    result = json.loads(path.read_text())
    result["usage"]["input_tokens"] = 999999
    path.write_text(runner.encoded(result))
    seal = json.loads((trial / "seal.json").read_text())
    seal["result.json"] = runner.digest(path.read_bytes())
    (trial / "seal.json").write_text(runner.encoded(seal))
    with pytest.raises(ValueError, match="does not match raw calls"):
        runner.report(output)
