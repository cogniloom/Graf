import json
from pathlib import Path

from .db import Store, dump, ident

DEFAULTS = dict(
    segment_chars=6000,
    overlap_chars=300,
    max_file_bytes=100_000_000,
    max_attachment_depth=8,
    max_attachments=1000,
    max_attachment_bytes=200_000_000,
    parser_timeout=180,
    ocr_languages="eng",
    ocr="auto",
    tessdata=None,
    excludes=[],
    identifiers=[],
    names=[],
    terms=[],
    rules_version="mechanical-v2",
    max_input_bytes=100_000,
    lease_seconds=1200,
    max_attempts=3,
)


def initialize(state, root, overrides=None):
    root, state = Path(root).resolve(strict=True), Path(state).resolve()
    if not root.is_dir() or state.is_relative_to(root):
        raise ValueError("State must be outside the source directory")
    # WAL depends on local shared memory. Reject known network mounts on Linux.
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if (
            len(fields) > 2
            and fields[2] in {"nfs", "nfs4", "cifs", "smb3", "sshfs", "fuse.sshfs"}
            and state.is_relative_to(fields[1])
        ):
            raise ValueError("Network state filesystem is unsupported")
    cfg = {**DEFAULTS, **(overrides or {}), "root": str(root)}
    if (
        cfg["segment_chars"] < 32
        or cfg["segment_chars"] > 10000
        or cfg["overlap_chars"] < 0
        or cfg["overlap_chars"] > 1000
    ):
        raise ValueError("Invalid segment/context budget")
    if cfg["max_file_bytes"] < 1 or cfg["max_input_bytes"] < 4096:
        raise ValueError("Invalid file/input budget")
    validate_config(cfg)
    store = Store(state)
    with store.write():
        existing = store.db.execute("SELECT configuration_json FROM corpora").fetchone()
        if existing and json.loads(existing[0]) != cfg:
            raise ValueError("Configuration is immutable; initialize another state directory")
        store.db.execute(
            "INSERT OR IGNORE INTO corpora VALUES(?,?,?)", (ident("C", str(root)), str(root), dump(cfg))
        )
        store.audit("initialized", {"configuration": cfg})
    return store


def configure(store, overrides):
    """Version future parsing/rules; never rewrite existing extractions or snapshots."""
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULTS):
        raise ValueError("Only documented configuration fields can change; corpus root is fixed")
    cfg = {**store.config(), **overrides}
    validate_config(cfg)
    with store.write():
        store.db.execute("UPDATE corpora SET configuration_json=?", (dump(cfg),))
        store.audit("configuration_changed", {"configuration": cfg, "applies_to": "future snapshots only"})
    return cfg


def validate_config(cfg):
    import re

    for key in (
        "segment_chars",
        "overlap_chars",
        "max_file_bytes",
        "max_attachment_depth",
        "max_attachments",
        "max_attachment_bytes",
        "parser_timeout",
        "max_input_bytes",
        "lease_seconds",
        "max_attempts",
    ):
        if type(cfg[key]) is not int or cfg[key] < (
            0 if key in ("overlap_chars", "max_attachment_depth", "max_attachments") else 1
        ):
            raise ValueError("Invalid positive integer configuration: " + key)
    if (
        not 32 <= cfg["segment_chars"] <= 10000
        or cfg["overlap_chars"] > 1000
        or cfg["max_input_bytes"] < 32768
    ):
        raise ValueError("Invalid segment/input budget (minimum input 32768 bytes)")
    if cfg["ocr"] not in ("auto", "off") or not re.fullmatch(
        r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", cfg["ocr_languages"]
    ):
        raise ValueError("Invalid OCR configuration")
    for grammar in cfg["identifiers"]:
        if not grammar.get("namespace") or not grammar.get("pattern"):
            raise ValueError("Identifier grammar needs namespace and pattern")
        re.compile(grammar["pattern"])
    if not all(isinstance(x, str) for key in ("excludes", "names", "terms") for x in cfg[key]):
        raise ValueError("Expected string list")
