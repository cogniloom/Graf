"""Replaceable, bounded parser subprocess; no model, retrieval or network client."""

from __future__ import annotations

import base64
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

SUPPORTED = {
    ".txt",
    ".md",
    ".pdf",
    ".docx",
    ".eml",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}


def signature(cfg):
    versions = {
        p: importlib.metadata.version(p) for p in ("pypdf", "python-docx", "pillow", "beautifulsoup4")
    }
    for cmd in ("tesseract", "pdftoppm"):
        try:
            p = subprocess.run(
                [cmd, "--version" if cmd == "tesseract" else "-v"], capture_output=True, timeout=5
            )
            versions[cmd] = (p.stdout + p.stderr).decode(errors="replace").splitlines()[0]
        except (OSError, subprocess.TimeoutExpired):
            versions[cmd] = "unavailable"
    if cfg.get("tessdata"):
        import hashlib

        for language in cfg["ocr_languages"].split("+"):
            path = Path(cfg["tessdata"]) / (language + ".traineddata")
            versions["language:" + language] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "unavailable"
            )
    return {
        "adapter": "native-v2",
        "versions": versions,
        "ocr_languages": cfg["ocr_languages"],
        "ocr": cfg["ocr"],
    }


def parse(data, suffix, cfg):
    if suffix not in SUPPORTED:
        return dict(
            sections=[],
            warnings=["Unsupported format: " + suffix],
            attachments=[],
            artifacts=[],
            status="unsupported",
        )
    if suffix in (".txt", ".md"):
        from .worker import extract

        return extract(data, suffix, cfg, None)
    with tempfile.TemporaryDirectory(prefix="evidencekg-parse-") as tmp:
        path = Path(tmp)
        (path / "input").write_bytes(data)
        (path / "config.json").write_text(json.dumps(cfg))
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")}
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
            result = json.loads((path / "result.json").read_text())
            for child in result["attachments"]:
                child["data"] = base64.b64decode(child["data"]) if child["data"] is not None else None
            for artifact in result["artifacts"]:
                artifact["data"] = base64.b64decode(artifact["data"])
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            return dict(sections=[], warnings=[str(exc)], attachments=[], artifacts=[], status="failed")
