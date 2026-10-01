import subprocess

from evidencekg.config import DEFAULTS
from evidencekg.parsers import signature


def test_default_ocr_model_install_and_replacement_invalidate_cache(tmp_path, monkeypatch):
    original = subprocess.run

    def run(args, **kwargs):
        if args == ["tesseract", "--list-langs"]:
            assert "TESSDATA_PREFIX" not in kwargs["env"]
            return subprocess.CompletedProcess(
                args, 0, f'List of available languages in "{tmp_path}" (0):\n'.encode(), b""
            )
        return original(args, **kwargs)

    monkeypatch.setenv("TESSDATA_PREFIX", "/not/the/worker/directory")
    monkeypatch.setattr(subprocess, "run", run)
    missing = signature(DEFAULTS)
    model = tmp_path / "eng.traineddata"
    model.write_bytes(b"installed model")
    installed = signature(DEFAULTS)
    model.write_bytes(b"replaced model")
    replaced = signature(DEFAULTS)
    assert missing["versions"]["language:eng"] == "unavailable"
    assert missing != installed != replaced
