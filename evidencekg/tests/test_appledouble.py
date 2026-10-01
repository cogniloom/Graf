import json
import struct

import pytest

from evidencekg.config import DEFAULTS
from evidencekg.parsers import parse
from evidencekg.parsers.appledouble import extract_appledouble


def sidecar(entry_id=9, offset=38, length=4):
    return (
        struct.pack(">II16sHIII", 0x00051607, 0x00020000, b"Mac OS X", 1, entry_id, offset, length) + b"meta"
    )


@pytest.mark.parametrize("suffix", [".pdf", ".jpg", ""])
def test_sidecar_content_overrides_misleading_extension(suffix):
    result = parse(sidecar(), suffix, DEFAULTS)
    assert result["status"] == "partial"
    assert json.loads(result["sections"][0]["text"])["entries"] == [
        {"entry_id": 9, "offset": 38, "length": 4}
    ]
    assert "stored separately" in result["warnings"][0]
    assert not result["attachments"]


@pytest.mark.parametrize(
    "data", [sidecar()[:20], sidecar()[:30], sidecar(1), sidecar(offset=0), sidecar(length=999)]
)
def test_invalid_sidecars_are_not_successful_metadata(data):
    with pytest.raises(ValueError):
        extract_appledouble(data)
