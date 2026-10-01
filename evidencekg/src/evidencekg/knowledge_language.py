"""Shared EN/DE lexical meanings with exact offsets and explicit ambiguity.

These are source observations/candidate lexical mappings, not facts about an
event. No host locale, current date, translation model or remote service is used.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import unicodedata
from bisect import bisect_right
from decimal import Decimal, InvalidOperation

VERSION = "bilingual-normalization-v1"
MONTHS = {
    1: {"en": ["January", "Jan"], "de": ["Januar", "Jan", "Jänner"]},
    2: {"en": ["February", "Feb"], "de": ["Februar", "Feb", "Feber"]},
    3: {"en": ["March", "Mar"], "de": ["März", "Maerz", "Mär", "Mrz"]},
    4: {"en": ["April", "Apr"], "de": ["April", "Apr"]},
    5: {"en": ["May"], "de": ["Mai"]},
    6: {"en": ["June", "Jun"], "de": ["Juni", "Jun"]},
    7: {"en": ["July", "Jul"], "de": ["Juli", "Jul"]},
    8: {"en": ["August", "Aug"], "de": ["August", "Aug"]},
    9: {"en": ["September", "Sep", "Sept"], "de": ["September", "Sep", "Sept"]},
    10: {"en": ["October", "Oct"], "de": ["Oktober", "Okt"]},
    11: {"en": ["November", "Nov"], "de": ["November", "Nov"]},
    12: {"en": ["December", "Dec"], "de": ["Dezember", "Dez"]},
}


def fold(text):
    return unicodedata.normalize("NFC", text).casefold()


MONTH_ALIASES = {}
for _number, _languages in MONTHS.items():
    for _language, _aliases in _languages.items():
        for _alias in _aliases:
            _entry = MONTH_ALIASES.setdefault(fold(_alias), {"month": _number, "languages": []})
            _entry["languages"].append(_language)

MONTH_PATTERN = (
    "(?:"
    + "|".join(
        re.escape(k)
        for k in sorted(
            set(MONTH_ALIASES) | {unicodedata.normalize("NFD", k) for k in MONTH_ALIASES},
            key=len,
            reverse=True,
        )
    )
    + r")\.?"
)
YEAR = r"(?:[1-9]\d{3})"
DAY = r"(?:[0-3]?\d)(?:st|nd|rd|th|\.)?"
# Named dates accept either language's month in either syntactic order. This is
# intentional: mixed-language sentences need not choose one document locale.
DATE_PATTERN = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[./]\d{{1,2}}[./]{YEAR}|"
    rf"{DAY}\s+{MONTH_PATTERN}\s+{YEAR}|{MONTH_PATTERN}\s+{DAY},?\s+{YEAR})"
)
DATE_RE = re.compile(rf"(?<![\w./-]){DATE_PATTERN}(?![\w/-])", re.IGNORECASE)
MONTH_YEAR_RE = re.compile(rf"(?<!\w)(?P<month>{MONTH_PATTERN})\s+(?P<year>{YEAR})(?!\w)", re.IGNORECASE)
MONTH_RE = re.compile(rf"(?<!\w){MONTH_PATTERN}(?!\w)", re.IGNORECASE)
RELATIVE_RE = re.compile(
    r"\b(?:today|tomorrow|yesterday|heute|morgen|gestern|"
    r"(?:next|last) (?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|"
    r"(?:nächste[nr]?|letzte[nr]?) (?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag))\b",
    re.IGNORECASE,
)
PREDICATE_ALIASES = {
    "approval": [
        "approval",
        "approve",
        "approved",
        "approving",
        "Genehmigung",
        "genehmigen",
        "genehmigt",
        "genehmigte",
        "Bewilligung",
        "bewilligt",
    ],
    "cancellation": [
        "cancellation",
        "cancel",
        "cancelled",
        "canceled",
        "Stornierung",
        "storniert",
        "stornierte",
        "annulliert",
        "Annullierung",
    ],
    "payment": ["payment", "pay", "paid", "Zahlung", "bezahlt", "bezahlte", "beglichen", "beglich"],
    "decision": [
        "decision",
        "decide",
        "decided",
        "Entscheidung",
        "entscheiden",
        "entschieden",
        "Beschluss",
        "beschlossen",
    ],
    "deadline": ["deadline", "due date", "Frist", "Fälligkeit", "Termin"],
    "dependency": [
        "depends on",
        "requires",
        "dependency",
        "abhängig von",
        "hängt ab von",
        "benötigt",
        "Abhängigkeit",
    ],
}
TERM_ALIASES = {fold(word): key for key, words in PREDICATE_ALIASES.items() for word in words}
TERM_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(TERM_ALIASES, key=len, reverse=True)) + r")(?!\w)",
    re.IGNORECASE,
)
ENTITY_TYPES = {
    "order": "order",
    "bestellung": "order",
    "auftrag": "order",
    "invoice": "invoice",
    "rechnung": "invoice",
    "request": "request",
    "antrag": "request",
    "anfrage": "request",
    "ticket": "ticket",
    "contract": "contract",
    "vertrag": "contract",
    "policy": "policy",
    "richtlinie": "policy",
    "regelung": "policy",
}
ENTITY_RE = re.compile(
    r"(?<!\w)(?P<type>"
    + "|".join(ENTITY_TYPES)
    + r")\s+#?(?P<value>[A-Za-z0-9][A-Za-z0-9_-]{0,79})(?![\w-])",
    re.IGNORECASE,
)


def date_readings(surface, language_hint=None):
    """Return all supported calendar readings; never insert today's year/day."""
    raw = fold(surface.strip())
    readings = set()

    def add(year, month, day):
        try:
            readings.add(dt.date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            pass

    if match := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw):
        add(*match.groups())
    elif match := re.fullmatch(r"(\d{1,2})([./])(\d{1,2})\2(\d{4})", raw):
        a, separator, b, year = match.groups()
        add(year, b, a)
        if not (separator == "." and language_hint == "de"):
            add(year, a, b)
    else:
        match = re.fullmatch(rf"(?P<day>{DAY})\s+(?P<month>{MONTH_PATTERN})\s+(?P<year>{YEAR})", raw)
        if not match:
            match = re.fullmatch(rf"(?P<month>{MONTH_PATTERN})\s+(?P<day>{DAY}),?\s+(?P<year>{YEAR})", raw)
        if match:
            month = MONTH_ALIASES[match["month"].rstrip(".")]["month"]
            day = re.match(r"\d+", match["day"])[0]
            add(match["year"], month, day)
    return sorted(readings)


def _observed(start, end, text, category, alternatives, **extra):
    return dict(
        start=start,
        end=end,
        quote=text[start:end],
        category=category,
        alternatives=alternatives,
        normalization_status="resolved"
        if len(alternatives) == 1
        else "ambiguous"
        if alternatives
        else "unresolved",
        **extra,
    )


def calendar_mentions(text):
    covered = []
    for match in DATE_RE.finditer(text):
        alternatives = date_readings(match.group())
        covered.append(match.span())
        yield _observed(
            *match.span(),
            text,
            "date",
            alternatives,
            precision="day",
            role="unassigned",
            concepts=sorted(
                {
                    concept
                    for value in alternatives
                    for concept in ("date:" + value, "date:" + value[:7], f"month:{int(value[5:7])}")
                }
            ),
            issue=None if alternatives else "invalid_calendar_date",
        )
    date_starts = [a for a, _ in covered]

    def contains(spans, starts, offset):
        i = bisect_right(starts, offset) - 1
        return i >= 0 and offset < spans[i][1]

    partial = []
    for match in MONTH_YEAR_RE.finditer(text):
        if contains(covered, date_starts, match.start()):
            continue
        # IGNORECASE also matches Unicode variants (e.g. dotless i) that
        # casefold does not map to our vocabulary. Do not guess their meaning.
        info = MONTH_ALIASES.get(fold(match["month"]).rstrip("."))
        if info is None:
            continue
        month = info["month"]
        year = int(match["year"])
        value = f"{year:04d}-{month:02d}"
        partial.append(match.span())
        yield _observed(
            *match.span(),
            text,
            "date",
            [value],
            precision="month",
            role="unassigned",
            concepts=["date:" + value, f"month:{month}"],
            interval={
                "start": value + "-01",
                "end_inclusive": value + f"-{calendar.monthrange(year, month)[1]}",
            },
        )
    covered = sorted(covered + partial)
    starts = [a for a, _ in covered]
    for match in MONTH_RE.finditer(text):
        info = MONTH_ALIASES.get(fold(match.group()).rstrip("."))
        if info is None:
            continue
        # Modal 'may' must not become a month. Named full dates above are explicit.
        if fold(match.group()) in {"may", "march"} and not contains(covered, starts, match.start()):
            before = text[max(0, match.start() - 12) : match.start()].strip().casefold()
            if not match.group()[0].isupper() and not before.endswith(("in", "im", "during")):
                continue
        yield _observed(
            *match.span(),
            text,
            "month",
            [info["month"]],
            precision="month_of_year",
            languages=sorted(set(info["languages"])),
            concepts=[f"month:{info['month']}"],
            interpretation="lexical_mapping_candidate; may also be a name or other word sense",
        )
    for match in RELATIVE_RE.finditer(text):
        yield _observed(
            *match.span(),
            text,
            "relative_date",
            [],
            precision="unresolved",
            role="unassigned",
            concepts=[],
            issue="explicit_source_time_anchor_required",
        )


def number_readings(surface):
    """Exact decimal strings, including EN/DE/Swiss forms; retain separator ambiguity."""
    raw = surface.strip().replace("\u00a0", " ").replace("\u202f", " ").replace("’", "'")
    sign = ""
    if raw.startswith(("-", "+")):
        sign, raw = raw[0], raw[1:]
    candidates = set()

    def add(value):
        try:
            result = Decimal(sign + value)
            if result.is_finite():
                # Decimal.normalize uses the active precision context and can
                # round large source amounts. Only strip insignificant zeros.
                value = format(result, "f")
                candidates.add(value.rstrip("0").rstrip(".") if "." in value else value)
        except InvalidOperation:
            pass

    # Explicit apostrophe/space grouping must be complete groups of three.
    for grouping in ("'", " "):
        if grouping in raw:
            if not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(grouping)}\d{{3}})+(?:[.,]\d{{1,2}})?", raw):
                return []
            raw = raw.replace(grouping, "")
    if "." in raw and "," in raw:
        decimal = "." if raw.rfind(".") > raw.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        if re.fullmatch(rf"\d{{1,3}}(?:{re.escape(grouping)}\d{{3}})+{re.escape(decimal)}\d{{1,2}}", raw):
            add(raw.replace(grouping, "").replace(decimal, "."))
    elif any(separator in raw for separator in ".,"):
        separator = "." if "." in raw else ","
        if re.fullmatch(rf"\d+{re.escape(separator)}\d{{1,3}}", raw):
            add(raw.replace(separator, "."))
        if re.fullmatch(rf"\d{{1,3}}(?:{re.escape(separator)}\d{{3}})+", raw):
            add(raw.replace(separator, ""))
    elif raw.isdigit():
        add(raw)
    return sorted(candidates, key=Decimal)


CURRENCY = r"(?:CHF|EUR|USD|GBP|CAD|AUD|€|£|\$)"
NUMBER = r"[+-]?\d+(?:(?:[.,'’\u00a0\u202f]| (?=\d{3}(?:\D|$)))\d+)*"
MONEY_RE = re.compile(
    rf"(?<![\w.,])(?:(?P<currency_before>{CURRENCY})\s*(?P<before>{NUMBER})|(?P<after>{NUMBER})\s*(?P<currency_after>{CURRENCY}))(?!\w)",
    re.IGNORECASE,
)


def money_mentions(text):
    for match in MONEY_RE.finditer(text):
        currency = (match["currency_before"] or match["currency_after"]).upper()
        currency = {"€": "EUR", "£": None, "$": None}.get(currency, currency)
        amounts = number_readings(match["before"] or match["after"])
        # No cross-product is promoted into a payment event.
        yield _observed(
            *match.span(),
            text,
            "money",
            amounts,
            currency=currency,
            currency_status="explicit" if currency else "ambiguous_symbol",
            concepts=[f"amount:{currency}:{amount}" for amount in amounts] if currency else [],
            issue=None if amounts else "invalid_number_format",
        )


def observations(text):
    """Independent span recognizers allow multiple languages in one sentence."""
    yield from calendar_mentions(text)
    yield from money_mentions(text)
    for match in TERM_RE.finditer(text):
        value = TERM_ALIASES.get(fold(match.group()))
        if value is None:
            continue
        yield _observed(
            *match.span(),
            text,
            "topic",
            [value],
            concepts=["topic:" + value],
            interpretation="lexical_candidate; no event or decision asserted",
        )
    for match in ENTITY_RE.finditer(text):
        entity_type = ENTITY_TYPES.get(fold(match["type"]))
        if entity_type is None:
            continue
        value = entity_type + ":" + match["value"]
        yield _observed(
            *match.span(),
            text,
            "entity_surface",
            [value],
            concepts=["entity:" + value],
            interpretation="type vocabulary normalized; real identity unresolved",
        )


def query_concepts(text):
    """Candidate expansion only; never changes literal/exhaustive text search."""
    concepts = {concept for observed in observations(text) for concept in observed["concepts"]}
    # A bare month query is a useful candidate lookup even for lowercase English
    # homonyms. This does not classify 'may approve' in evidence as a calendar date.
    month = MONTH_ALIASES.get(fold(text.strip()).rstrip("."))
    if month:
        concepts.add(f"month:{month['month']}")
    return sorted(concepts)
