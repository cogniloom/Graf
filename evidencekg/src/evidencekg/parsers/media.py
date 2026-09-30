"""Local media metadata and explicitly unverified offline speech transcription."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import wave

from .formats import MEDIA


def extract_media(data, suffix, cfg, directory):
    if suffix not in MEDIA:
        return None
    result = dict(
        sections=[],
        attachments=[],
        artifacts=[],
        warnings=["Visual content and speaker identity remain unreviewed"],
        status="partial",
    )
    metadata = {}
    if suffix in (".wav", ".wave"):
        try:
            with wave.open(io.BytesIO(data)) as audio:
                metadata = dict(
                    channels=audio.getnchannels(),
                    sample_rate=audio.getframerate(),
                    duration_seconds=audio.getnframes() / audio.getframerate(),
                    sample_width=audio.getsampwidth(),
                )
        except (wave.Error, EOFError):
            result["warnings"].append(
                "Native WAV metadata unavailable; media probe and speech decoder still attempted"
            )
    else:
        import mutagen

        try:
            audio = mutagen.File(io.BytesIO(data))
            if audio is not None:
                metadata["format"] = type(audio).__name__
                for field in ("length", "bitrate", "sample_rate", "channels"):
                    if hasattr(audio.info, field):
                        metadata[field] = getattr(audio.info, field)
                if audio.tags:
                    # Never stringify binary artwork payloads as searchable prose.
                    metadata["tags"] = {
                        str(k): str(v)
                        for k, v in audio.tags.items()
                        if isinstance(v, (str, int, float))
                        or (isinstance(v, list) and all(isinstance(x, str) for x in v))
                    }
                    for key, value in audio.tags.items():
                        if hasattr(value, "text"):
                            metadata["tags"][str(key)] = str(value.text)
        except Exception:
            result["warnings"].append("Native media tags could not be read")
    if shutil.which("ffprobe"):
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                "wav,mp3,flac,ogg,mov,matroska,webm,avi,asf,aac,aiff,amr,au",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(directory / "input"),
            ],
            capture_output=True,
            timeout=min(cfg["parser_timeout"], 30),
        )
        if probe.returncode == 0:
            details = json.loads(probe.stdout)
            details.get("format", {}).pop("filename", None)
            metadata["probe"] = details
        else:
            result["warnings"].append("Media probe failed; container may be damaged or unsupported")
    if not metadata:
        result["status"] = "failed"
        result["warnings"].append("No readable media metadata; ffprobe may be required")
    else:
        result["sections"].append(
            dict(
                text=json.dumps(metadata, ensure_ascii=False, indent=2),
                locator={"kind": "media_metadata"},
                modality="native",
            )
        )
    from .transcription import transcribe

    try:
        speech = transcribe(directory, cfg)
        for key in ("sections", "warnings", "artifacts"):
            result[key].extend(speech[key])
        if speech["artifacts"]:
            result["status"] = "partial"
    except Exception as exc:
        result["warnings"].append(
            f"Speech transcription failed; no transcript indexed: {type(exc).__name__}: {str(exc)[:300]}"
        )
    return result
