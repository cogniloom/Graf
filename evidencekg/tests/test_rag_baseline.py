import hashlib
import json
import subprocess

import pytest

from evidencekg import rag_baseline as rag


@pytest.fixture(scope="module")
def runtime():
    if not rag.DEFAULT_PYTHON.is_file():
        pytest.skip("Optional docworm Python runtime absent")
    probe = subprocess.run(
        [
            str(rag.DEFAULT_PYTHON),
            "-I",
            "-c",
            "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('lance') else 77)",
        ],
        capture_output=True,
        timeout=30,
    )
    if probe.returncode == 77:
        pytest.skip("Optional pylance package absent")
    assert probe.returncode == 0, "Present optional runtime is broken"
    return rag.DEFAULT_PYTHON


def write_fixture(tmp_path):
    segments = []
    for sid, text in (
        ("s1", "The violet deadline is Friday."),
        ("s2", "Payment was not approved."),
        ("s3", "Payment was approved."),
        ("s4", "Repeated marker."),
        ("s5", "Repeated marker."),
    ):
        segments.append(
            {
                "id": sid,
                "text": text,
                "document_version_id": "doc-" + sid,
                "extraction_id": "ext-" + sid,
                "text_sha": hashlib.sha256(text.encode()).hexdigest(),
                "locators": [{"page": 1}],
            }
        )
    frozen = {"snapshot_id": "synthetic-frozen", "segments": segments}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(frozen))
    return path, frozen


def test_real_fts_singleton_negation_scope_and_provenance(tmp_path, runtime):
    source, frozen = write_fixture(tmp_path)
    root = tmp_path / "isolated"
    manifest = rag.build(source, root, python=runtime)
    assert manifest["segment_count"] == 5
    result = rag.search(root, "violet", snapshot_id=frozen["snapshot_id"], python=runtime)
    assert result["segments"] == frozen["segments"][:1]
    assert result["reasons"] == {}
    assert result["candidate_count"] == 1
    assert result["provenance"]["backend"] == "direct-lance-native-fts"
    assert result["provenance"]["python"].startswith("3.")
    assert result["provenance"]["pylance"]
    assert result["provenance"]["adapter_sha256"]
    assert "probe passed" in result["provenance"]["network_guard"]
    assert "probe passed" in result["provenance"]["inference_guard"]
    assert result["scores"]["s1"] > 0  # Native Lance relevance is higher-is-better.
    assert result == rag.search(root, "violet", snapshot_id=frozen["snapshot_id"], python=runtime)
    negative = rag.search(root, "not", snapshot_id=frozen["snapshot_id"], python=runtime)
    assert negative["segments"] == frozen["segments"][1:2]
    empty = rag.search(root, "absentword", snapshot_id=frozen["snapshot_id"], python=runtime)
    assert empty["candidate_count"] == empty["selected_count"] == 0
    # Administrator source edits after build cannot silently expand the frozen scope.
    source.write_text("{}")
    assert result == rag.search(root, "violet", snapshot_id=frozen["snapshot_id"], python=runtime)
    with pytest.raises(RuntimeError, match="subprocess failed"):
        rag.search(root, "violet", snapshot_id="other-snapshot", python=runtime)
    with pytest.raises(FileExistsError):
        source.write_text(json.dumps(frozen))
        rag.build(source, root, python=runtime)


def test_real_budget_ties_and_tamper_rejection(tmp_path, runtime):
    source, frozen = write_fixture(tmp_path)
    root = tmp_path / "isolated"
    rag.build(source, root, python=runtime)
    result = rag.search(root, "marker", snapshot_id=frozen["snapshot_id"], limit=1, python=runtime)
    assert result["segments"] == frozen["segments"][3:4]
    assert result["candidate_count"] == 2 and result["omitted_count"] == 1
    assert result["evidence_bytes"] <= result["max_evidence_bytes"] == 60000
    small = rag.search(
        root, "marker", snapshot_id=frozen["snapshot_id"], max_evidence_bytes=30, python=runtime
    )
    assert small["segments"] == [] and small["omitted_count"] == 2
    marker = root / "index" / "unexpected"
    marker.write_text("tamper")
    with pytest.raises(RuntimeError, match="subprocess failed"):
        rag.search(root, "marker", snapshot_id=frozen["snapshot_id"], python=runtime)
    marker.unlink()
    frozen_path = root / "frozen.json"
    original = frozen_path.read_bytes()
    frozen_path.write_bytes(original + b" ")
    with pytest.raises(RuntimeError, match="subprocess failed"):
        rag.search(root, "marker", snapshot_id=frozen["snapshot_id"], python=runtime)
    frozen_path.write_bytes(original)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["provenance"]["pylance"] = "changed-runtime"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="subprocess failed"):
        rag.search(root, "marker", snapshot_id=frozen["snapshot_id"], python=runtime)


def test_missing_runtime_and_timeout_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(rag.OptionalRuntimeUnavailable):
        rag.search(tmp_path, "benign", snapshot_id="synthetic", python=tmp_path / "absent-python")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("test", 1)

    monkeypatch.setattr(rag.subprocess, "run", timed_out)
    with pytest.raises(RuntimeError, match="timed out"):
        rag.search(tmp_path, "benign", snapshot_id="synthetic", python=__file__, timeout=1)


def test_invalid_frozen_inputs_fail_before_creating_destination(tmp_path):
    source, frozen = write_fixture(tmp_path)
    root = tmp_path / "isolated"
    frozen["segments"].append(frozen["segments"][0])
    source.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="Duplicate"):
        rag.build(source, root)
    assert not root.exists()
    frozen["segments"].pop()
    frozen["segments"][0]["text"] = "changed"
    source.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="hash differs"):
        rag.build(source, root)
    assert not root.exists()


@pytest.mark.parametrize(
    "query,kwargs", [("", {}), ("term", {"limit": True}), ("term", {"max_evidence_bytes": 60001})]
)
def test_invalid_search_parameters(tmp_path, query, kwargs):
    with pytest.raises(ValueError):
        rag.search(tmp_path, query, snapshot_id="synthetic", **kwargs)
