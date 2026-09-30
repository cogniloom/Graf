"""Linear feature memberships. No inferred legal relationships or identity merges."""

import datetime
import re

from .db import dump, ident

VERSION = "mechanical-v1"
LIMITATIONS = [
    "truth of either assertion",
    "same person or event",
    "independent corroboration",
    "legal applicability",
]


def matches(text, cfg):
    for pattern in cfg["identifiers"]:
        for m in re.finditer(pattern["pattern"], text):
            group = "value" if "value" in m.groupdict() else 0
            yield (
                "identifier",
                pattern["namespace"],
                m.group(group),
                m.start(group),
                m.end(group),
                {},
                "configured-identifier",
            )
    patterns = [
        ("email", "surface", r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
        ("url", "literal", r'https?://[^\s<>"\)]+'),
        ("document_reference", "path", r"\[\[([^\]\n]+)\]\]"),
    ]
    for kind, namespace, regex in patterns:
        for m in re.finditer(regex, text):
            group = 1 if kind == "document_reference" else 0
            yield kind, namespace, m.group(group), m.start(group), m.end(group), {}, kind
    for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b", text):
        raw = m.group()
        alternatives = []
        formats = ["%Y-%m-%d"] if "-" in raw else ["%d/%m/%Y", "%m/%d/%Y"]
        for fmt in formats:
            try:
                alternatives.append(datetime.datetime.strptime(raw, fmt).date().isoformat())
            except ValueError:
                pass
        alternatives = sorted(set(alternatives))
        if alternatives:
            yield (
                "date",
                "calendar" if len(alternatives) == 1 else "ambiguous-numeric",
                alternatives[0] if len(alternatives) == 1 else raw,
                m.start(),
                m.end(),
                {"alternatives": alternatives},
                "absolute-date",
            )
    for kind, values in [("name_surface", cfg["names"]), ("term", cfg["terms"])]:
        for value in values:
            if value:
                for m in re.finditer(re.escape(value), text):
                    yield (
                        kind,
                        "literal-surface",
                        value,
                        m.start(),
                        m.end(),
                        {"identity_resolved": False},
                        "surface",
                    )
    # Whitespace collapse only: case, punctuation, accents, negation retained.
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        value = " ".join(m.group().split())
        if value:
            yield "paragraph", "whitespace-v1", value, m.start(), m.end(), {}, "paragraph"


def index_extraction(store, extraction, text, cfg, blob):
    segments = store.rows("SELECT * FROM segments WHERE extraction_id=? ORDER BY ordinal", (extraction,))

    def add(kind, namespace, value, lo, hi, ambiguity, rule):
        feature = ident("F", kind, namespace, value, cfg["rules_version"])
        store.db.execute(
            "INSERT OR IGNORE INTO features VALUES(?,?,?,?,?)",
            (feature, kind, value, namespace, cfg["rules_version"]),
        )
        for segment in segments:
            start, end = max(lo, segment["char_start"]), min(hi, segment["char_end"])
            if end <= start and not (lo == hi == 0 and segment["ordinal"] == 0):
                continue
            start -= segment["char_start"]
            end -= segment["char_start"]
            raw = segment["text"][start:end]
            oid = ident("O", feature, segment["id"], start, end, rule)
            store.db.execute(
                "INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    oid,
                    feature,
                    segment["id"],
                    start,
                    end,
                    raw,
                    rule,
                    cfg["rules_version"],
                    dump({**ambiguity, "canonical_start": lo, "canonical_end": hi}),
                ),
            )

    for item in matches(text, cfg):
        add(*item)
    if blob:
        store.verify_blob(blob)  # checksum-verified captured bytes; copies remain separate documents.
        add("original_blob", "sha256", blob, 0, min(1, len(text)), {}, "captured-byte-identity")
    seen_headers = set()
    for segment in segments:
        for loc in __import__("json").loads(segment["locator_json"]):
            locator = loc.get("locator", {})
            key = (loc["start"], loc["end"])
            if locator.get("kind") == "email_header" and key not in seen_headers:
                seen_headers.add(key)
                header = locator["header"].lower()
                kind = (
                    "message_id"
                    if header == "message-id"
                    else "reply_reference"
                    if header in ("in-reply-to", "references")
                    else None
                )
                if kind:
                    for m in re.finditer(r"<[^<>\s]+>", text[loc["start"] : loc["end"]]):
                        add(
                            kind,
                            "message-id-exact",
                            m.group(),
                            loc["start"] + m.start(),
                            loc["start"] + m.end(),
                            {},
                            "email-header",
                        )
