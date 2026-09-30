"""Exercise binary parser transport under a real address-space budget."""

import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from evidencekg.config import DEFAULTS
from evidencekg.parsers import parse
from evidencekg.parsers.archives import extract_archive
from evidencekg.parsers.payloads import read_payload, write_payload


def test_archive_payloads_are_outside_json(tmp_path):
    stream = io.BytesIO()
    payload = bytes(range(256)) * 4096
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("PaDö/😀.bin", payload)
        archive.writestr("empty.bin", b"")
        archive.writestr("../unsafe.bin", b"unsafe")
    result = extract_archive(stream.getvalue(), ".zip", DEFAULTS, tmp_path)
    assert len(json.dumps(result)) < 4096
    first, empty, unsafe = result["attachments"]
    assert read_payload(tmp_path, first["data_file"], len(payload)) == payload
    assert read_payload(tmp_path, empty["data_file"], 0) == b""
    assert unsafe["data"] is None and "gap" in unsafe
    parsed = parse(stream.getvalue(), ".zip", {**DEFAULTS, "ocr": "off"})
    assert parsed["attachments"][0]["data"] == payload
    assert parsed["attachments"][0]["name"] == "😀.bin"
    assert parsed["attachments"][1]["data"] == b""
    assert "data_file" not in parsed["attachments"][0]
    assert parsed["status"] == "partial"


def test_payload_rejects_paths_links_and_oversized_files(tmp_path):
    ref = write_payload(tmp_path, b"payload")["data_file"]
    with pytest.raises(ValueError, match="size"):
        read_payload(tmp_path, ref, 6)
    for name in ("../input", "/etc/passwd", "input", "payload-" + "a" * 31):
        with pytest.raises(ValueError, match="name"):
            read_payload(tmp_path, name, 100)
    link = tmp_path / ("payload-" + "a" * 32)
    link.symlink_to(tmp_path / ref)
    with pytest.raises(OSError):
        read_payload(tmp_path, link.name, 100)
    link.unlink()
    os.mkfifo(link)
    with pytest.raises(ValueError, match="type"):
        read_payload(tmp_path, link.name, 100)


def test_archive_result_under_address_space_limit(tmp_path):
    # 60 MB of payload fits comfortably; the former base64 + Unicode JSON copies
    # exceed 256 MB. Run in a separate process so pytest's imports cannot skew it.
    archive_path = tmp_path / "payload.zip"
    block = b"x" * 1_000_000
    expected = hashlib.sha256(block * 30).hexdigest()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("😀-one.bin", "two.bin"):
            with archive.open(name, "w") as member:
                for _ in range(30):
                    member.write(block)
    script = """
import hashlib, json, resource, sys
from pathlib import Path
from evidencekg.config import DEFAULTS
from evidencekg.parsers import parse
resource.setrlimit(resource.RLIMIT_AS, (256_000_000, 2_000_000_000))
r = parse(Path(sys.argv[1]).read_bytes(), '.zip', {**DEFAULTS, 'ocr': 'off'})
assert r['status'] == 'ready', r['warnings']
assert len(r['attachments']) == 2
assert all(len(a['data']) == 30_000_000 for a in r['attachments'])
assert all(hashlib.sha256(a['data']).hexdigest() == sys.argv[2] for a in r['attachments'])
print(json.dumps({'bytes': sum(len(a['data']) for a in r['attachments'])}))
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script, str(archive_path), expected],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["bytes"] == 60_000_000


def test_recapturing_large_blob_has_bounded_memory(vault):
    import tracemalloc

    store = vault[1]
    data = b"x" * (8 * 1024 * 1024)
    key = store.put(data)
    tracemalloc.start()
    try:
        assert store.put(data) == key
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2 * 1024 * 1024
    path = store.state / "objects" / key[:2] / key
    for corrupt in (data[:-1], data + b"x", b"y" + data[1:]):
        path.write_bytes(corrupt)
        with pytest.raises(ValueError, match="collision or corrupt"):
            store.put(data)
