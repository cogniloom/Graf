"""Replaceable, bounded offline parser subprocess; no remote inference or retrieval."""

from __future__ import annotations

import base64
import importlib.metadata
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .formats import SUPPORTED as SUPPORTED  # Public capability inventory.
from .payloads import read_payload


def signature(cfg):
    versions = {
        p: importlib.metadata.version(p)
        for p in (
            "pypdf",
            "python-docx",
            "pillow",
            "beautifulsoup4",
            "openpyxl",
            "xlrd",
            "striprtf",
            "defusedxml",
            "libarchive-c",
            "mutagen",
            "olefile",
        )
    }
    speech_model = None
    calibration_sha = None
    if cfg.get("transcription_calibration"):
        import hashlib

        path = Path(cfg["transcription_calibration"])
        calibration_sha = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file() and path.stat().st_size <= 1_000_000
            else "unavailable"
        )
        versions["calibration_implementation_sha256"] = hashlib.sha256(
            Path(__file__).parent.parent.joinpath("knowledge_calibration.py").read_bytes()
        ).hexdigest()
    if cfg.get("transcription_model"):
        from .transcription import model_identity

        try:
            speech_model = model_identity(cfg["transcription_model"])
        except (OSError, ValueError):
            speech_model = "unavailable"
        for dependency in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "numpy", "tokenizers"):
            try:
                versions[dependency] = importlib.metadata.version(dependency)
            except importlib.metadata.PackageNotFoundError:
                versions[dependency] = "unavailable"
    for cmd in ("tesseract", "pdftoppm", "antiword", "ffprobe", "ffmpeg"):
        try:
            p = subprocess.run(
                [
                    cmd,
                    "--version"
                    if cmd == "tesseract"
                    else (
                        "-version" if cmd in ("ffprobe", "ffmpeg") else ("-h" if cmd == "antiword" else "-v")
                    ),
                ],
                capture_output=True,
                timeout=5,
            )
            versions[cmd] = (p.stdout + p.stderr).decode(errors="replace").splitlines()[0]
        except (OSError, IndexError, subprocess.TimeoutExpired):
            versions[cmd] = "unavailable"
    tessdata = cfg.get("tessdata")
    if not tessdata:
        # Match the worker's sanitized environment, including the absence of
        # TESSDATA_PREFIX. Installing a previously missing default model must
        # invalidate cached OCR gaps even when the Tesseract binary is unchanged.
        try:
            available = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True,
                timeout=5,
                env={k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")},
            )
            match = re.search(
                r'List of available languages in "([^"\n]+)"',
                (available.stdout + available.stderr).decode(errors="replace"),
            )
            if available.returncode == 0 and match:
                tessdata = match.group(1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if tessdata:
        import hashlib

        for language in cfg["ocr_languages"].split("+"):
            path = Path(tessdata) / (language + ".traineddata")
            versions["language:" + language] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "unavailable"
            )
    else:
        for language in cfg["ocr_languages"].split("+"):
            versions["language:" + language] = "unavailable"
    try:
        import libarchive.ffi

        versions["libarchive"] = str(libarchive.ffi.version_number())
    except (ImportError, OSError):
        versions["libarchive"] = "unavailable"
    import hashlib

    implementation = hashlib.sha256()
    for module in sorted(Path(__file__).parent.glob("*.py")):
        implementation.update(module.name.encode())
        implementation.update(module.read_bytes())
    return {
        "implementation_sha256": implementation.hexdigest(),
        "adapter": "native-v4",
        "transcription_model_sha256": speech_model,
        "transcription_calibration_sha256": calibration_sha,
        "versions": versions,
        "ocr_languages": cfg["ocr_languages"],
        "ocr": cfg["ocr"],
    }


def parse(data, suffix, cfg):
    # Keep the common plain-text path cheap without bypassing binary sniffing.
    if suffix.lower() in (".txt", ".md"):
        try:
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = data.decode(encoding)
            if all(c.isprintable() or c in "\r\n\t\f" for c in text):
                return dict(
                    sections=[dict(text=text, locator={"kind": "text"}, modality="native")],
                    warnings=[],
                    attachments=[],
                    artifacts=[],
                    status="ready",
                )
        except UnicodeError:
            pass
    with tempfile.TemporaryDirectory(prefix="evidencekg-parse-") as tmp:
        path = Path(tmp)
        (path / "input").write_bytes(data)
        (path / "config.json").write_text(json.dumps(cfg))
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")}
        env.update(
            OMP_NUM_THREADS="2",
            OPENBLAS_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            TOKENIZERS_PARALLELISM="false",
        )
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        shared_group = os.environ.get("GRAF_INGEST_PROCESS_GROUP") == str(os.getpgrp())
        try:
            p = subprocess.Popen(
                [sys.executable, "-m", "evidencekg.parsers.worker", str(path), suffix],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=tmp,
                start_new_session=not shared_group,
            )
            try:
                stdout, stderr = p.communicate(timeout=cfg["parser_timeout"])
            except BaseException:
                if shared_group:
                    p.kill()  # The application supervisor owns the whole ingestion group.
                else:
                    os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                raise
            if p.returncode:
                raise ValueError("Parser failed: " + stderr.decode(errors="replace")[-1500:])
            result = json.loads((path / "result.json").read_text(encoding="utf-8"))
            for child in result["attachments"]:
                child["data"] = (
                    read_payload(path, child.pop("data_file"), cfg["max_attachment_bytes"])
                    if "data_file" in child
                    else (base64.b64decode(child["data"]) if child["data"] is not None else None)
                )
            for artifact in result["artifacts"]:
                artifact["data"] = (
                    read_payload(path, artifact.pop("data_file"), cfg["max_file_bytes"])
                    if "data_file" in artifact
                    else base64.b64decode(artifact["data"])
                )
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return dict(sections=[], warnings=[str(exc)], attachments=[], artifacts=[], status="failed")
