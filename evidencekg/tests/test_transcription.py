import copy
import io
import json
import math
import os
import shutil
import subprocess
import wave

import pytest

from evidencekg.config import DEFAULTS, initialize, validate_config
from evidencekg.ingest import ingest
from evidencekg.parsers import parse, signature
from evidencekg.parsers.transcription import confidence, model_identity, render_segments
from evidencekg.retrieval import API


def candidate(probability=0.97, **overrides):
    return dict(
        start=0.0,
        end=1.0,
        text=" A fictional quokka.",
        avg_logprob=-0.05,
        no_speech_prob=0.01,
        compression_ratio=1.2,
        words=[
            dict(start=0.0, end=0.1, word=" A", probability=probability),
            dict(start=0.1, end=0.5, word=" fictional", probability=probability),
            dict(start=0.5, end=1.0, word=" quokka.", probability=probability),
        ],
        **overrides,
    )


def wav(seconds=0.1):
    output = io.BytesIO()
    with wave.open(output, "wb") as source:
        source.setnchannels(1)
        source.setsampwidth(2)
        source.setframerate(16000)
        source.writeframes(b"\x00\x00" * int(seconds * 16000))
    return output.getvalue()


def test_confidence_is_explicitly_uncalibrated_and_timestamped():
    sections, decisions = render_segments([candidate()], 1.0, 0.8)
    assert decisions[0]["level"] == "high"
    assert decisions[0]["calibrated"] is False
    assert decisions[0]["score"] == pytest.approx(math.exp(-0.05))
    assert "not accuracy" in sections[0]["text"]
    assert "unverified" in sections[0]["text"]
    assert sections[0]["locator"]["end_seconds"] == 1.0
    assert sections[0]["modality"] == "asr"


@pytest.mark.parametrize(
    "field,value",
    [
        ("avg_logprob", float("nan")),
        ("no_speech_prob", float("inf")),
        ("no_speech_prob", 0.8),
        ("compression_ratio", 5.0),
        ("words", []),
        ("words", [{"probability": -1}]),
        ("words", [{"probability": None}]),
    ],
)
def test_uncertain_or_invalid_metrics_never_become_searchable_guesses(field, value):
    raw = candidate()
    raw[field] = value
    sections, decisions = render_segments([raw], 1, 0.8)
    assert decisions[0]["withheld"]
    assert decisions[0]["level"] == "low"
    assert "quokka" not in json.dumps(sections)
    assert "quokka" in raw["text"]  # Raw candidate remains available for the separate audit artifact.


def test_weak_word_withholds_whole_sentence_including_possible_negation():
    raw = candidate()
    raw["words"][1]["probability"] = 0.2
    sections, decisions = render_segments([raw], 1, 0.8)
    assert decisions[0]["score"] == 0.2
    assert "quokka" not in json.dumps(sections)
    assert "[Unclear speech:" in sections[0]["text"]


def test_threshold_boundary_and_medium_level():
    assert confidence(candidate(0.8), 0.8)["level"] == "medium"
    assert confidence(candidate(0.7999), 0.8)["withheld"]


@pytest.mark.parametrize("start,end", [(-1, 1), (0, float("nan")), (0, 2), (1, 0)])
def test_invalid_timestamps_fail_closed(start, end):
    raw = candidate()
    raw.update(start=start, end=end)
    with pytest.raises(ValueError, match="timestamps"):
        render_segments([raw], 1, 0.8)


def test_missing_word_alignment_cannot_publish_unscored_text():
    raw = candidate()
    raw["text"] += " invented unscored claim"
    sections, decisions = render_segments([raw], 1, 0.8)
    assert decisions[0]["withheld"]
    assert "invented" not in json.dumps(sections)


@pytest.mark.parametrize(
    "key,value",
    [
        ("transcription_model", "small"),
        ("transcription_model", "/tmp/../model"),
        ("transcription_min_confidence", float("nan")),
        ("transcription_min_confidence", True),
        ("transcription_min_confidence", 0.1),
        ("transcription_max_seconds", 0),
        ("transcription_max_seconds", 3601),
        ("transcription_language", "en;command"),
    ],
)
def test_configuration_rejects_invalid_speech_settings(key, value):
    with pytest.raises(ValueError, match="Transcription"):
        validate_config(DEFAULTS | {key: value})


def test_model_fingerprint_changes_with_tokenizer_and_weights(tmp_path):
    for name in ("model.bin", "tokenizer.json", "config.json"):
        (tmp_path / name).write_bytes(b"synthetic model file")
    first = model_identity(str(tmp_path))
    (tmp_path / "model.bin").write_bytes(b"changed model")
    second = model_identity(str(tmp_path))
    (tmp_path / "tokenizer.json").write_bytes(b"changed tokenizer")
    assert len({first, second, model_identity(str(tmp_path))}) == 3
    assert signature(DEFAULTS | {"transcription_model": str(tmp_path)})[
        "transcription_model_sha256"
    ] == model_identity(str(tmp_path))


def test_missing_model_keeps_metadata_and_explicit_gap(tmp_path):
    result = parse(wav(), ".wav", DEFAULTS | {"transcription_model": str(tmp_path)})
    assert result["status"] == "partial"
    assert result["sections"][0]["locator"]["kind"] == "media_metadata"
    assert not any(s["modality"] == "asr" for s in result["sections"])
    assert any("Speech transcription failed" in w for w in result["warnings"])


def test_ingestion_exposes_asr_confidence_and_excludes_withheld_text(tmp_path, monkeypatch):
    # Deterministic plumbing check, not an inference/accuracy test.
    from evidencekg import parsers

    source = tmp_path / "sources"
    source.mkdir()
    (source / "audio.wav").write_bytes(wav())
    high = candidate()
    low = copy.deepcopy(high)
    low.update(start=1.0, end=2.0, text=" Low-confidence secret.")
    low["words"] = [dict(start=1.0, end=2.0, word=" Low-confidence secret.", probability=0.2)]
    sections, _ = render_segments([high, low], 2.0, 0.8)
    monkeypatch.setattr(
        parsers,
        "parse",
        lambda *args: dict(
            sections=sections,
            artifacts=[],
            attachments=[],
            status="partial",
            warnings=["Synthetic confidence plumbing fixture"],
        ),
    )
    store = initialize(tmp_path / "state", source)
    try:
        snapshot = ingest(store)
        api = API(store)
        assert api.search(snapshot, "quokka", "literal")["total"] == 1
        assert api.search(snapshot, "secret", "literal")["total"] == 0
        assert {r["modality"] for r in store.rows("SELECT modality FROM segments")} == {"asr"}
        assert all(r["status"] == "partial" for r in store.rows("SELECT status FROM segments"))
    finally:
        store.close()


def test_app_speech_options_propagate_and_validate(tmp_path):
    from evidencekg.app.config import AppConfig

    cfg = AppConfig(
        home=tmp_path / "home",
        database_config=tmp_path / "db.json",
        models=tmp_path / "models",
        token_file=tmp_path / "token",
        ui_dist=tmp_path / "ui",
        transcription_model=str(tmp_path / "speech"),
        transcription_language="de",
        transcription_min_confidence=0.9,
    )
    assert cfg.transcription_options()["transcription_min_confidence"] == 0.9
    validate_config(DEFAULTS | cfg.transcription_options())


MODEL = os.environ.get("EVIDENCEKG_TEST_SPEECH_MODEL")


@pytest.mark.skipif(not MODEL, reason="Set EVIDENCEKG_TEST_SPEECH_MODEL to existing local weights")
@pytest.mark.parametrize(
    "language,voice,spoken,distinctive_word",
    [
        (
            "en",
            "en-us",
            "This is a fictional recording. The blue notebook is on the wooden table.",
            "notebook",
        ),
        (
            "de",
            "de",
            "Dies ist eine erfundene Aufnahme. Das blaue Notizbuch liegt auf dem Tisch.",
            "notizbuch",
        ),
    ],
)
def test_real_local_speech_silence_video_and_confidence(tmp_path, language, voice, spoken, distinctive_word):
    if not shutil.which("espeak") or not shutil.which("ffmpeg"):
        pytest.skip("espeak and ffmpeg needed for synthetic speech fixtures")
    root = tmp_path / "sources"
    root.mkdir()
    audio = root / "speech.wav"
    subprocess.run(
        [
            "espeak",
            "-v",
            voice,
            "-s",
            "145",
            "-w",
            str(audio),
            spoken,
        ],
        check=True,
    )
    cfg = DEFAULTS | {"transcription_model": MODEL, "transcription_language": language}
    result = parse(audio.read_bytes(), ".wav", cfg)
    assert result["status"] == "partial"
    assert result["artifacts"], result["warnings"]
    audit = json.loads(result["artifacts"][0]["data"])
    assert audit["candidates"]
    assert audit["confidence_is_accuracy_probability"] is False
    assert len(audit["model_sha256"]) == 64
    for section in result["sections"]:
        if section["modality"] == "asr":
            assert "unverified" in section["text"]
            assert section["locator"]["confidence"]["calibrated"] is False
    # Repeat real inference with a stricter policy: no model text may escape.
    strict = parse(audio.read_bytes(), ".wav", cfg | {"transcription_min_confidence": 1.0})
    assert strict["artifacts"], strict["warnings"]
    assert all(s["locator"]["confidence"]["withheld"] for s in strict["sections"] if s["modality"] == "asr")
    assert all(distinctive_word not in s["text"].lower() for s in strict["sections"])
    silent = parse(wav(2), ".wav", cfg)
    assert silent["artifacts"], silent["warnings"]
    assert json.loads(silent["artifacts"][0]["data"])["candidates"] == []
    assert not any(s["modality"] == "asr" for s in silent["sections"])
    # Video follows the same timestamped audio path; visual understanding remains a gap.
    video = tmp_path / "speech.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=1",
            "-i",
            str(audio),
            "-shortest",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(video),
        ],
        check=True,
    )
    movie = parse(video.read_bytes(), ".mkv", cfg)
    assert movie["artifacts"], movie["warnings"]
    assert json.loads(movie["artifacts"][0]["data"])["candidates"]
    floating = tmp_path / "floating.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio), "-c:a", "pcm_f32le", str(floating)], check=True
    )
    float_result = parse(floating.read_bytes(), ".wav", cfg)
    assert float_result["artifacts"], float_result["warnings"]
    oversized = parse(audio.read_bytes(), ".wav", cfg | {"transcription_max_seconds": 1})
    assert not oversized["artifacts"]
    assert any("duration limit exceeded" in w for w in oversized["warnings"])


def test_stale_model_fingerprint_rejects_before_inference(tmp_path):
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / name).write_text("synthetic")
    result = parse(
        wav(),
        ".wav",
        DEFAULTS
        | {"transcription_model": str(tmp_path), "_transcription_model_identity": "stale-model-identity"},
    )
    assert not result["artifacts"]
    assert any("changed since ingestion began" in w for w in result["warnings"])


def test_missing_tokenizer_never_falls_back_to_remote_model(tmp_path):
    (tmp_path / "model.bin").write_text("synthetic")
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="tokenizer.json"):
        model_identity(str(tmp_path))
