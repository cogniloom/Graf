from types import SimpleNamespace

import pytest

from evidencekg.accuracy import (
    PLAN_INSTRUCTION,
    PLAN_SCHEMA,
    AccuracyDiscovery,
    DurableCalls,
    validate_assessments,
)


class Worker:
    identity = {"adapter": "test"}
    model = "deterministic-test"
    effort = "none"

    def __init__(self):
        self.count = 0
        self.fail = False

    def call(self, stage, payload, schema, instruction):
        self.count += 1
        if self.fail:
            raise RuntimeError("transport stopped")
        if stage == "accuracy_plan":
            return {"queries": ["German"], "phrases": [], "facets": ["condition"]}
        return {
            "assessments": {
                u["unit_id"]: {
                    "relevance": 3,
                    "facet_flags": {"0": True},
                    "evidence_blocks": ["B0"],
                    "uncertainty": "",
                }
                for u in payload["units"]
            }
        }


def test_resume_reuses_only_validated_exact_request(tmp_path):
    w = Worker()
    calls = DurableCalls(tmp_path, w)
    a = calls.call("accuracy_plan", {"question": "A"}, PLAN_SCHEMA, PLAN_INSTRUCTION)
    assert calls.call("accuracy_plan", {"question": "A"}, PLAN_SCHEMA, PLAN_INSTRUCTION) == a
    assert w.count == 1
    calls.call("accuracy_plan", {"question": "B"}, PLAN_SCHEMA, PLAN_INSTRUCTION)
    assert w.count == 2


def test_unknown_or_failed_attempt_never_retried(tmp_path):
    w = Worker()
    w.fail = True
    calls = DurableCalls(tmp_path, w)
    with pytest.raises(RuntimeError):
        calls.call("accuracy_plan", {}, PLAN_SCHEMA, PLAN_INSTRUCTION)
    with pytest.raises(ValueError, match="explicit recovery"):
        calls.call("accuracy_plan", {}, PLAN_SCHEMA, PLAN_INSTRUCTION)
    assert w.count == 1


def test_budget_rejected_before_model(tmp_path):
    w = Worker()
    calls = DurableCalls(tmp_path, w, max_input_bytes=1000)
    with pytest.raises(ValueError, match="budget"):
        calls.call("accuracy_plan", {"question": "A" * 10000}, PLAN_SCHEMA, PLAN_INSTRUCTION)
    assert w.count == 0


def test_quotes_and_unit_coverage_validated():
    import jsonschema

    units = [{"unit_id": "u", "text": "not approved"}]
    row = {"relevance": 3, "facet_flags": {"0": True}, "evidence_blocks": ["B9"], "uncertainty": ""}
    with pytest.raises(jsonschema.ValidationError):
        validate_assessments({"assessments": {"u": row}}, units, ["condition"])
    row["evidence_blocks"] = ["B0"]
    assert validate_assessments({"assessments": {"u": row}}, units, ["condition"])[0]["quotes"] == [
        "not approved"
    ]
    with pytest.raises(jsonschema.ValidationError):
        validate_assessments(
            {"assessments": {"u": row}}, units + [{"unit_id": "v", "text": "other"}], ["condition"]
        )


def test_full_candidate_partition_and_original_output(tmp_path):
    text = "only if approved " * 900
    segment = {"id": "S1", "extraction_id": "E1", "document_version_id": "D1", "text": text}
    collector = SimpleNamespace(
        snapshot_id="N1", collect=lambda *a, **kw: {"segments": [segment], "remaining": 0}
    )
    w = Worker()
    calls = DurableCalls(tmp_path, w)
    out = AccuracyDiscovery(collector, calls).retrieve("condition?")
    assert out["segments"] == [segment]
    assert out["delivery"]["source_units_assessed"] == 3
    assert not out["uncovered_facets"]
    for ref in out["model_assessments"]["S1"]["evidence"]:
        assert text[ref["start"] : ref["end"]] == ref["quote"]


def test_exhaustive_scope_overflow_not_silently_ranked(tmp_path):
    collector = SimpleNamespace(snapshot_id="N1", collect=lambda *a, **kw: {"rejected": True})
    with pytest.raises(ValueError, match="exhaustive"):
        AccuracyDiscovery(collector, DurableCalls(tmp_path, Worker())).retrieve("q", scope_document_ids=["D"])


def test_small_budget_splits_singleton_before_dispatch(tmp_path):
    segment = {"id": "S", "document_version_id": "D", "extraction_id": "E", "text": "xy" * 4000}
    collector = SimpleNamespace(snapshot_id="N", collect=lambda *a, **kw: {"segments": [segment]})
    calls = DurableCalls(tmp_path, Worker(), max_input_bytes=5000)
    result = AccuracyDiscovery(collector, calls).retrieve("condition?")
    assert result["segments"] == [segment]
    assert result["delivery"]["source_units_assessed"] > 2


@pytest.mark.parametrize("length, starts", [(2400, [1200]), (8400, [1200, 7200])])
def test_selected_repeated_block_retains_occurrence_offset(tmp_path, length, starts):
    class LaterBlockWorker(Worker):
        def call(self, stage, payload, schema, instruction):
            result = super().call(stage, payload, schema, instruction)
            if stage == "accuracy_assess":
                for row in result["assessments"].values():
                    row["evidence_blocks"] = ["B1"]
            return result

    segment = {"id": "S", "extraction_id": "E", "document_version_id": "D", "text": "x" * length}
    collector = SimpleNamespace(snapshot_id="N", collect=lambda *a, **kw: {"segments": [segment]})
    result = AccuracyDiscovery(collector, DurableCalls(tmp_path, LaterBlockWorker())).retrieve("condition?")
    assert result["model_assessments"]["S"]["evidence"] == [
        {"start": start, "end": start + 1200, "quote": "x" * 1200} for start in starts
    ]
    assert result["segments"] == [segment]
