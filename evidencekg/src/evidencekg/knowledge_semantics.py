"""Bounded bilingual event/temporal grammar and paragraph boundaries.

Rules assert what a source says. They never resolve pronouns, omitted arguments,
speaker identity, policy precedence, or real-world truth.
"""

from __future__ import annotations

import datetime as dt
import re
from bisect import bisect_right

from .knowledge_language import (
    DATE_PATTERN,
    ENTITY_RE,
    ENTITY_TYPES,
    date_readings,
    money_mentions,
)

NAME = r"[^\W\d_][\w\u0300-\u036f.-]*(?: [^\W\d_][\w\u0300-\u036f.-]*){0,4}"
TYPED = r"(?:" + "|".join(ENTITY_TYPES) + r")\s+#?[A-Za-z0-9][A-Za-z0-9_-]{0,79}"
PREDICATES = {"validity", "supersession", "delivery", "rejection", "dependency"}
WEEKDAYS = {
    name: n
    for n, names in enumerate(
        (
            ("monday", "montag"),
            ("tuesday", "dienstag"),
            ("wednesday", "mittwoch"),
            ("thursday", "donnerstag"),
            ("friday", "freitag"),
            ("saturday", "samstag"),
            ("sunday", "sonntag"),
        )
    )
    for name in names
}


def sentences(text):
    """Split only strong punctuation/newline boundaries; never split date/amount dots."""
    start = 0
    protected = [m.span() for m in re.finditer(DATE_PATTERN, text, re.I)]
    starts = [span[0] for span in protected]
    for match in re.finditer(r"\n+|[.!?](?:[\"”»])?(?=\s+[A-ZÄÖÜÖ„\"]|\s*$)", text):
        end = match.start() if match.group().startswith("\n") else match.end()
        position = bisect_right(starts, match.start()) - 1
        if position >= 0 and match.start() < protected[position][1]:
            continue
        prefix = text[start : match.start()].rsplit(" ", 1)[-1]
        if match.group().startswith(".") and (
            prefix in {"Dr", "Prof", "Mr", "Mrs", "Ms", "bzw", "Nr"}
            or (len(prefix) == 1 and prefix.isalpha())
        ):
            continue
        raw = text[start:end]
        if raw.strip():
            a = start + len(raw) - len(raw.lstrip())
            yield a, a + len(raw.strip()), raw.strip()
        start = match.end()
    raw = text[start:]
    if raw.strip():
        a = start + len(raw) - len(raw.lstrip())
        yield a, a + len(raw.strip()), raw.strip()


def quotation_spans(text):
    """Balanced double quotation scopes, including continued sentences."""
    stack, spans = [], []
    pairs = {'"': '"', "„": "“", "“": "”", "«": "»", "»": "«"}
    for index, character in enumerate(text):
        if stack and character == stack[-1][1]:
            begin, _, speaker = stack.pop()
            spans.append((begin, index + 1, speaker))
        elif character in pairs:
            prefix = text[max(0, index - 160) : index]
            match = re.search(rf"({NAME}) (?:said|wrote|stated|sagte|schrieb|erklärte):\s*$", prefix, re.I)
            stack.append((index, pairs[character], match[1] if match else None))
    spans.extend((begin, len(text), speaker) for begin, _, speaker in stack)
    return sorted(spans)


def relative_day(surface, anchor):
    if not anchor:
        return None
    try:
        day = dt.date.fromisoformat(anchor)
    except (ValueError, TypeError):
        return None
    offsets = {"today": 0, "heute": 0, "tomorrow": 1, "morgen": 1, "yesterday": -1, "gestern": -1}
    if surface.casefold() in offsets:
        try:
            return (day + dt.timedelta(days=offsets[surface.casefold()])).isoformat()
        except OverflowError:
            return None
    # 'Next Friday' has competing conventions; preserve it unresolved rather
    # than silently choosing the next occurrence versus the following week.
    return None


def entity(surface):
    match = ENTITY_RE.fullmatch(surface)
    return (ENTITY_TYPES[match["type"].casefold()], match["value"], match["type"]) if match else None


def record(subject, predicate, language, **values):
    parsed = entity(subject)
    if parsed is None:
        return None
    kind, value, surface = parsed
    return dict(
        entity_key=kind + ":" + value,
        entity_type=kind,
        entity_value=value,
        entity_type_surface=surface,
        predicate=predicate,
        language=language,
        polarity="positive",
        modality="asserted",
        condition=None,
        applicable_on=None,
        applicable_date_surface=None,
        applicable_date_candidates=[],
        actor_surface=None,
        actor_identity="unresolved",
        attribution="source_document",
        negation_scope="none",
        extraction_rule=language + "-event-v1",
        **values,
    )


def parse(quote, base, locator=None):
    locator = locator or {}
    body = quote.strip().rstrip(".!")
    if "?" in body:
        return None
    quoted = body.startswith(('"', "“", "„", "'", ">", "»", "«"))
    content = body.lstrip("> ").strip("\"“”„»«' ").rstrip(".!")
    speaker = None
    attribution = re.fullmatch(
        rf"(?P<speaker>{NAME}) (?:said|wrote|stated|sagte|schrieb|erklärte):\s*(?P<statement>.+)",
        content,
        re.I,
    )
    if attribution:
        speaker = attribution["speaker"]
        content = attribution["statement"].strip("\"“”„»«' ").rstrip(".!")
    parsed = base(quote)
    if parsed is None and (speaker or quoted or locator.get("quotation_depth")):
        parsed = base(content)
    if parsed is None:
        match = re.fullmatch(
            rf"(?P<subject>{TYPED}) (?P<verb>applies|is effective|gilt|ist gültig) (?:from|ab|vom) (?P<start>{DATE_PATTERN})(?: (?P<until>until|through|bis(?: einschließlich)?) (?P<end>{DATE_PATTERN}))?",
            content,
            re.I,
        )
        if match:
            language = "de" if match["verb"].casefold() in {"gilt", "ist gültig"} else "en"
            starts = date_readings(match["start"], language)
            ends = date_readings(match["end"], language) if match["end"] else []
            valid = (
                len(starts) == 1
                and (not match["end"] or len(ends) == 1)
                and (not ends or ends[0] >= starts[0])
            )
            parsed = record(
                match["subject"],
                "validity",
                language,
                validity=dict(
                    start=starts[0] if valid else None,
                    end=ends[0] if valid and ends else None,
                    start_candidates=starts,
                    end_candidates=ends,
                    status="explicit_interval" if valid else "unresolved",
                    end_inclusive=True
                    if match["until"] and match["until"].casefold() in {"through", "bis einschliesslich"}
                    else None,
                    source_surface=content,
                ),
            )
    # Full, explicitly scoped validity interval on any supported statement.
    if parsed is None:
        pattern = rf"(?P<body>.+?) (?P<marker>valid from|effective from|gültig ab|gültig vom) (?P<start>{DATE_PATTERN})(?: (?P<until>until|through|bis(?: einschließlich)?) (?P<end>{DATE_PATTERN}))?"
        match = re.fullmatch(pattern, content, re.I)
        if match:
            parsed = base(match["body"])
            if parsed:
                language = "de" if match["marker"].lower().startswith("gültig") else "en"
                starts, ends = (
                    date_readings(match["start"], language),
                    date_readings(match["end"], language) if match["end"] else [],
                )
                valid = (
                    len(starts) == 1
                    and (not match["end"] or len(ends) == 1)
                    and (not ends or ends[0] >= starts[0])
                )
                parsed["validity"] = dict(
                    start=starts[0] if valid else None,
                    end=ends[0] if valid and ends else None,
                    start_candidates=starts,
                    end_candidates=ends,
                    end_inclusive=True
                    if match["until"] and match["until"].casefold() in {"through", "bis einschliesslich"}
                    else None,
                    status="explicit_interval" if valid else "unresolved",
                    source_surface=match.group(),
                )
                parsed["extraction_rule"] += "+validity"
    # Relative dates only use an explicitly supplied authored-source day.
    if parsed is None:
        match = re.fullmatch(
            r"(.+?) (?:on |am )?(today|tomorrow|yesterday|heute|morgen|gestern)", content, re.I
        )
        if match and (parsed := base(match[1])):
            anchor = (
                locator.get("source_day")
                if not (locator.get("quotation_depth") or quoted or speaker)
                else None
            )
            resolved = relative_day(match[2], anchor)
            parsed.update(
                applicable_on=resolved,
                applicable_date_surface=match[2],
                applicable_date_candidates=[resolved] if resolved else [],
                temporal_anchor=dict(day=anchor, source="authored_message_header" if anchor else "unknown"),
            )
    if parsed is None:
        match = re.fullmatch(
            rf"(?P<subject>{TYPED}) (?P<verb>replaces|supersedes|ersetzt) (?P<object>{TYPED})(?: (?:from|ab|effective from) (?P<date>{DATE_PATTERN}))?",
            content,
            re.I,
        )
        if match:
            language = "de" if match["verb"].casefold() == "ersetzt" else "en"
            other = entity(match["object"])
            parsed = record(
                match["subject"],
                "supersession",
                language,
                object_key=other[0] + ":" + other[1],
                precedence="explicit_source_statement_only",
            )
            if match["date"]:
                readings = date_readings(match["date"], language)
                parsed.update(
                    applicable_on=readings[0] if len(readings) == 1 else None,
                    applicable_date_surface=match["date"],
                    applicable_date_candidates=readings,
                )
    if parsed is None:
        match = re.fullmatch(
            rf"(?P<subject>{TYPED}) (?P<verb>depends on|requires|hängt ab von|benötigt) (?P<object>{TYPED})",
            content,
            re.I,
        )
        if match:
            other = entity(match["object"])
            parsed = record(
                match["subject"],
                "dependency",
                "en" if match["verb"].casefold() in {"depends on", "requires"} else "de",
                object_key=other[0] + ":" + other[1],
            )
    if parsed is None:
        patterns = [
            (
                rf"(?P<payer>{NAME}) paid (?P<amount>.+?) to (?P<payee>{NAME}) for (?P<subject>{TYPED})(?: on (?P<date>{DATE_PATTERN}))?",
                "en",
            ),
            (
                rf"(?P<payer>{NAME}) zahlte (?P<amount>.+?) an (?P<payee>{NAME}) für (?P<subject>{TYPED})(?: am (?P<date>{DATE_PATTERN}))?",
                "de",
            ),
        ]
        for pattern, language in patterns:
            match = re.fullmatch(pattern, content, re.I)
            if not match or any(
                not part[0].isupper() for role in ("payer", "payee") for part in match[role].split()
            ):
                continue
            if any(base(f"{match[role]} approved order 1.") is None for role in ("payer", "payee")):
                continue
            money = list(money_mentions(match["amount"]))
            if len(money) != 1 or money[0]["quote"] != match["amount"]:
                continue
            parsed = record(
                match["subject"],
                "payment",
                language,
                event_type="payment",
                participants={
                    "payer": match["payer"],
                    "payee": match["payee"],
                    "invoice_or_subject": match["subject"],
                },
                amount=money[0],
            )
            parsed["amount"]["span_scope"] = "amount_surface_only"
            parsed["actor_surface"] = match["payer"]
            if match["date"]:
                readings = date_readings(match["date"], language)
                parsed.update(
                    applicable_on=readings[0] if len(readings) == 1 else None,
                    applicable_date_surface=match["date"],
                    applicable_date_candidates=readings,
                )
            break
    if parsed:
        if quoted or speaker or locator.get("quotation_depth"):
            parsed["attribution"] = "quoted_speaker_unresolved"
        if speaker or locator.get("attributed_speaker_surface"):
            parsed["speaker_surface"] = speaker or locator["attributed_speaker_surface"]
        role = locator.get("evidence_role", "body")
        parsed["evidence_role"] = role
        parsed["revision_state"] = locator.get("revision_state", "none")
        if (
            role in {"deleted_revision", "inserted_revision", "comment", "note"}
            or parsed["revision_state"] != "none"
        ):
            parsed.update(attribution="document_structure_qualified", modality="unresolved")
    return parsed


def temporal_relation(claim, day):
    interval = claim.get("validity")
    if not interval or interval["status"] != "explicit_interval":
        return "unknown"
    if day < interval["start"]:
        return "before_validity"
    end = interval["end"]
    if end and day > end:
        return "after_validity"
    if day == end and interval["end_inclusive"] is None:
        return "boundary_unresolved"
    return "within_stated_validity"
