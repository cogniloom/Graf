"""Offline speech recognition with abstention and uncalibrated decoder confidence."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import math
import subprocess
from pathlib import Path

POLICY = "speech-confidence-v2"
NOTICE = (
    "Automatic speech transcription is unverified. Model confidence is an uncalibrated "
    "decoder score, not a probability of correctness. Check the original audio before relying on it."
)
MODEL_FILES = (
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.txt",
    "vocabulary.json",
    "preprocessor_config.json",
)
MEDIA_FORMATS = "wav,mp3,flac,ogg,mov,matroska,webm,avi,asf,aac,aiff,amr,au"


def model_identity(model):
    """Fingerprint only local inference files, streaming large weights."""
    root = Path(model)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Transcription requires an existing absolute local model directory")
    hashes = {}
    for name in MODEL_FILES:
        path = root / name
        if not path.exists():
            if name in ("model.bin", "config.json", "tokenizer.json"):
                raise ValueError(f"Local transcription model is missing {name}")
            continue
        h = hashlib.sha256()
        with path.open("rb") as stream:
            before = path.stat()
            while block := stream.read(1024 * 1024):
                h.update(block)
            after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Transcription model changed while fingerprinting")
        hashes[name] = h.hexdigest()
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def confidence(segment, threshold):
    """Weakest word/token score: intentionally conservative, never calibrated accuracy."""
    reasons = []
    words = segment.get("words", [])
    try:
        avg = segment["avg_logprob"]
        no_speech = segment["no_speech_prob"]
        ratio = segment["compression_ratio"]
        probabilities = [w["probability"] for w in words]
        if (
            not words
            or any(
                type(x) not in (int, float) or not math.isfinite(x)
                for x in [avg, no_speech, ratio, *probabilities]
            )
            or avg > 0
            or not 0 <= no_speech <= 1
            or ratio < 0
            or any(not 0 <= p <= 1 for p in probabilities)
        ):
            raise ValueError("Invalid confidence metrics")
        score = min(math.exp(avg), *probabilities)
        if score < threshold:
            reasons.append("decoder_score_below_threshold")
        if no_speech > 0.35:
            reasons.append("possible_non_speech")
        if ratio > 2.4:
            reasons.append("repetitive_output")
    except (KeyError, TypeError, ValueError, OverflowError):
        score = None
        reasons.append("missing_or_invalid_confidence_metrics")
    critical = []
    for word in words if isinstance(words, list) else []:
        if not isinstance(word, dict) or len(critical) >= 128:
            continue
        value = word.get("word", "")
        value = value.strip().casefold() if isinstance(value, str) else ""
        score_value = word.get("probability")
        if type(score_value) not in (int, float) or not math.isfinite(score_value) or score_value < threshold:
            category = (
                "negation"
                if value in {"not", "no", "never", "nicht", "kein", "keine", "keinen", "niemals"}
                else "number_or_identifier"
                if any(c.isdigit() for c in value)
                else "word"
            )
            critical.append(
                dict(
                    category=category,
                    **{
                        key: word.get(key)
                        if type(word.get(key)) in (int, float) and math.isfinite(word[key])
                        else None
                        for key in ("start", "end")
                    },
                )
            )
    return dict(
        score=score,
        level="low" if reasons else ("high" if score >= 0.9 else "medium"),
        withheld=bool(reasons),
        reasons=reasons,
        method="minimum_word_probability_and_exp_avg_logprob",
        calibrated=False,
        threshold=threshold,
        uncertain_spans=critical,
        probability_target="transcription_fidelity",
        calibrated_probability=None,
        calibration_status="no_reference_calibration",
    )


def retry_uncertain(model, samples, segments, threshold, language, budget_seconds=30):
    """Retain actual alternate decodes under a bounded audio budget, never vote."""
    used, results = 0.0, []
    for index, segment in enumerate(segments):
        if not confidence(segment, threshold)["withheld"]:
            continue
        if (
            any(
                type(segment.get(key)) not in (int, float) or not math.isfinite(segment[key])
                for key in ("start", "end")
            )
            or not 0 <= segment["start"] < segment["end"] <= len(samples) / 16000 + 0.1
        ):
            results.append(dict(segment=index, status="retry_invalid_alignment"))
            continue
        duration = segment["end"] - segment["start"]
        if duration <= 0 or duration > 10 or used + duration > budget_seconds:
            results.append(dict(segment=index, status="retry_budget_or_duration_limit"))
            continue
        used += duration
        chunk = samples[int(segment["start"] * 16000) : int(segment["end"] * 16000)]
        try:
            generated, _ = model.transcribe(
                chunk,
                language=language,
                beam_size=1,
                temperature=0.0,
                condition_on_previous_text=False,
                word_timestamps=True,
                vad_filter=False,
                no_speech_threshold=None,
                log_prob_threshold=None,
                compression_ratio_threshold=None,
            )
            alternatives = []
            for other in generated:
                if len(alternatives) >= 100:
                    raise ValueError("Retry segment limit")
                if (
                    not all(math.isfinite(v) for v in (other.start, other.end, other.avg_logprob))
                    or not 0 <= other.start <= other.end <= duration + 0.1
                    or other.avg_logprob > 0
                ):
                    raise ValueError("Invalid retry metrics or alignment")
                alternatives.append(
                    dict(
                        text=other.text,
                        start=float(other.start) + segment["start"],
                        end=float(other.end) + segment["start"],
                        raw_avg_logprob=float(other.avg_logprob),
                    )
                )
            text = "".join(a["text"] for a in alternatives).strip()
            # Both outputs are interpretations of the SAME recording/model.
            results.append(
                dict(
                    segment=index,
                    status="alternate_decode",
                    beam_size=1,
                    alternatives=alternatives,
                    agrees_with_primary=text == segment["text"].strip(),
                    calibrated_probability=None,
                    support_group="same_recording_same_model",
                    publication_policy="audit_only_primary_withholding_unchanged",
                )
            )
        except (ValueError, RuntimeError):
            results.append(dict(segment=index, status="retry_failed_primary_retained"))
    return dict(audio_seconds_used=used, audio_seconds_budget=budget_seconds, results=results)


def render_segments(segments, duration, threshold):
    """Raw guesses stay in the audit artifact, never in indexed low-confidence text."""
    sections, decisions = [], []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        start, end = segment["start"], segment["end"]
        if (
            any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
            or start < previous_end - 0.05
            or start < 0
            or end < start
            or end > duration + 0.1
        ):
            raise ValueError("Invalid or overlapping transcription timestamps")
        previous_end = end
        decision = confidence(segment, threshold)
        words = segment.get("words", [])
        try:
            word_end = start
            for word in words:
                a, b = word["start"], word["end"]
                if (
                    not math.isfinite(a)
                    or not math.isfinite(b)
                    or a < word_end - 0.05
                    or a < start - 0.05
                    or b < a
                    or b > end + 0.05
                ):
                    raise ValueError("Invalid word timing")
                word_end = b
            if (
                not segment.get("text", "").strip()
                or "".join(w["word"] for w in words).strip() != segment["text"].strip()
            ):
                raise ValueError("Unaligned transcript text")
        except (KeyError, TypeError, ValueError):
            decision.update(level="low", withheld=True)
            decision["reasons"].append("missing_or_invalid_word_alignment")
        score = "unavailable" if decision["score"] is None else f"{decision['score']:.3f}"
        header = (
            f"[Automatic transcript {start:.2f}–{end:.2f}s; confidence {decision['level']}; "
            f"decoder score {score}, not accuracy; unverified]"
        )
        body = (
            "[Unclear speech: candidate wording withheld; listen to the original audio.]"
            if decision["withheld"]
            else segment["text"].strip()
        )
        locator = dict(
            kind="speech_transcript",
            segment=index,
            start_seconds=start,
            end_seconds=end,
            confidence=decision,
            review_required=True,
        )
        # Do not leak withheld guesses through searchable segment locators either.
        if not decision["withheld"]:
            locator["words"] = words
        sections.append(dict(text=header + "\n" + body, locator=locator, modality="asr"))
        decisions.append(dict(segment=index, **decision))
    return sections, decisions


def transcribe(directory, cfg):
    model_path = cfg.get("transcription_model")
    if not model_path:
        return dict(
            sections=[],
            artifacts=[],
            warnings=[
                "Speech not transcribed: configure a local transcription_model and install the transcription extra"
            ],
        )
    identity = model_identity(model_path)
    expected = cfg.get("_transcription_model_identity", identity)
    if identity != expected:
        raise ValueError("Transcription model changed since ingestion began")
    # Decode to bounded mono PCM first. Never let PyAV follow a playlist or decode
    # an unbounded recording in memory. One extra second detects oversized input.
    limit = cfg.get("transcription_max_seconds", 600)
    pcm = directory / "speech.pcm"
    decoded = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            MEDIA_FORMATS,
            "-i",
            str(directory / "input"),
            "-map",
            "0:a:0",
            "-vn",
            "-t",
            str(limit + 1),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            "-y",
            str(pcm),
        ],
        capture_output=True,
        timeout=min(cfg["parser_timeout"], 60),
    )
    if decoded.returncode:
        raise ValueError("Speech decode failed or media has no audio stream")
    size = pcm.stat().st_size
    if size > limit * 16000 * 2:
        raise ValueError("Speech duration limit exceeded; no truncated transcript was indexed")
    if not size:
        raise ValueError("Audio stream contains no samples")
    import numpy as np
    from faster_whisper import WhisperModel

    samples = np.fromfile(pcm, dtype="<i2").astype(np.float32) / 32768.0
    duration = len(samples) / 16000
    model = WhisperModel(
        model_path, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1, local_files_only=True
    )
    generated, info = model.transcribe(
        samples,
        task="transcribe",
        language=cfg.get("transcription_language"),
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=False,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        # Graf records/rejects uncertain output itself; do not silently discard a
        # model candidate because of the decoder's no-speech/logprob thresholds.
        no_speech_threshold=None,
        log_prob_threshold=None,
        compression_ratio_threshold=None,
    )
    candidates = []
    for segment in generated:
        if len(candidates) >= 10000:
            raise ValueError("Speech segment limit exceeded")
        candidates.append(
            dict(
                start=float(segment.start),
                end=float(segment.end),
                text=segment.text,
                avg_logprob=float(segment.avg_logprob),
                no_speech_prob=float(segment.no_speech_prob),
                compression_ratio=float(segment.compression_ratio),
                words=[
                    dict(
                        start=float(w.start), end=float(w.end), word=w.word, probability=float(w.probability)
                    )
                    for w in (segment.words or [])
                ],
            )
        )
    if model_identity(model_path) != identity:
        raise ValueError("Transcription model changed during extraction")
    threshold = cfg.get("transcription_min_confidence", 0.8)
    retries = retry_uncertain(model, samples, candidates, threshold, cfg.get("transcription_language"))
    if model_identity(model_path) != identity:
        raise ValueError("Transcription model changed during retry extraction")
    sections, decisions = render_segments(candidates, duration, threshold)
    calibration_artifact = None
    if cfg.get("transcription_calibration"):
        from ..knowledge_calibration import apply as apply_calibration

        path = Path(cfg["transcription_calibration"])
        if path.stat().st_size > 1_000_000:
            raise ValueError("Calibration artifact exceeds size limit")
        calibration_bytes = path.read_bytes()
        calibration_sha = hashlib.sha256(calibration_bytes).hexdigest()
        if cfg.get("_transcription_calibration_sha256", calibration_sha) != calibration_sha:
            raise ValueError("Calibration reference changed since ingestion began")
        calibration_model = json.loads(calibration_bytes)
        calibration_artifact = dict(
            name="speech-calibration.json", data=base64.b64encode(calibration_bytes).decode()
        )
        scope = dict(
            model_sha256=identity,
            language=cfg.get("transcription_language"),
            input_domain=cfg.get("transcription_input_domain"),
            probability_target="transcription_fidelity",
        )
        for section, decision in zip(sections, decisions, strict=True):
            estimate = apply_calibration(calibration_model, decision["score"], scope)
            # Calibration annotates fidelity only; it never releases withheld
            # wording, resolves identities, or turns a claim into a fact.
            decision["reference_calibration"] = estimate
            section["locator"]["confidence"]["reference_calibration"] = estimate
    warnings = [
        NOTICE,
        "Voice activity detection can miss speech; only the first audio stream is transcribed. Visual content and speaker identity are unreviewed",
    ]
    if not candidates:
        warnings.append(
            "No speech detected by the model; this does not prove that the recording contains no speech"
        )
    withheld = sum(d["withheld"] for d in decisions)
    if withheld:
        warnings.append(
            f"{withheld} speech segment(s) withheld from search because confidence or alignment was insufficient"
        )
    artifact = dict(
        policy=POLICY,
        model_sha256=identity,
        runtime=importlib.metadata.version("faster-whisper"),
        language=info.language,
        language_probability=float(info.language_probability),
        duration_seconds=duration,
        audio_stream="0:a:0",
        vad_retained_seconds=float(info.duration_after_vad),
        confidence_is_accuracy_probability=False,
        review_required=True,
        decisions=decisions,
        candidates=candidates,
        retries=retries,
    )
    encoded = json.dumps(artifact, ensure_ascii=False, allow_nan=False).encode()
    return dict(
        sections=sections,
        warnings=warnings,
        artifacts=[dict(name="speech-transcription.json", data=base64.b64encode(encoded).decode())]
        + ([calibration_artifact] if calibration_artifact else []),
    )
