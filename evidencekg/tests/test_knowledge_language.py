"""Independent EN/DE expectations, including ambiguous and adversarial readings."""

import unicodedata

import pytest

from evidencekg.knowledge import parse_clause
from evidencekg.knowledge_language import (
    calendar_mentions,
    date_readings,
    money_mentions,
    number_readings,
    observations,
    query_concepts,
)


@pytest.mark.parametrize(
    "number,en,de",
    [
        (1, "January", "Januar"),
        (2, "February", "Februar"),
        (3, "March", "März"),
        (4, "April", "April"),
        (5, "May", "Mai"),
        (6, "June", "Juni"),
        (7, "July", "Juli"),
        (8, "August", "August"),
        (9, "September", "September"),
        (10, "October", "Oktober"),
        (11, "November", "November"),
        (12, "December", "Dezember"),
    ],
)
def test_all_months_shared_meaning_original_spans(number, en, de):
    for month in (en, de, unicodedata.normalize("NFD", de)):
        text = f"Am 12. {month} 2026 / on {month} 12, 2026."
        rows = list(calendar_mentions(text))
        dates = [r for r in rows if r["category"] == "date"]
        assert len(dates) == 2
        assert all(r["alternatives"] == [f"2026-{number:02d}-12"] for r in dates)
        assert all(text[r["start"] : r["end"]] == r["quote"] for r in rows)
        assert f"month:{number}" in query_concepts(month.lower())


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("12. März 2026", ["2026-03-12"]),
        ("March 12th, 2026", ["2026-03-12"]),
        ("12 Maerz 2026", ["2026-03-12"]),
        ("2026-03-12", ["2026-03-12"]),
        ("03/04/2026", ["2026-03-04", "2026-04-03"]),
        ("03.04.2026", ["2026-03-04", "2026-04-03"]),
        ("29. Februar 2024", ["2024-02-29"]),
        ("29. Februar 2026", []),
        ("31 April 2026", []),
        ("12. März", []),
        ("next Friday", []),
        ("03/04.2026", []),
    ],
)
def test_date_readings_do_not_invent_calendar_parts(surface, expected):
    assert date_readings(surface) == expected


def test_partial_dates_relative_anchors_and_modal_homonyms():
    rows = list(calendar_mentions("März 2026; March 2026; next Friday; nächsten Freitag; morgen; tomorrow"))
    partial = [r for r in rows if r["category"] == "date"]
    assert len(partial) == 2
    assert all(r["precision"] == "month" and r["alternatives"] == ["2026-03"] for r in partial)
    assert all(r["interval"] == {"start": "2026-03-01", "end_inclusive": "2026-03-31"} for r in partial)
    relative = [r for r in rows if r["category"] == "relative_date"]
    assert len(relative) == 4
    assert all(
        r["alternatives"] == [] and r["issue"] == "explicit_source_time_anchor_required" for r in relative
    )
    assert not list(calendar_mentions("Alice may approve. They march forward."))


@pytest.mark.parametrize("surface", ["Maı", "MAİ", "Aprıl", "Aprİl", "Junı", "Julİ"])
def test_unicode_regex_month_variants_do_not_crash_or_guess(surface):
    assert date_readings(f"12 {surface} 2026") == []
    rows = list(calendar_mentions(f"{surface}; {surface} 2026; 12 {surface} 2026"))
    assert all(row["alternatives"] == [] for row in rows)
    assert query_concepts(surface) == []


@pytest.mark.parametrize("surface", ["decısion", "DECİSION", "ınvoice INV-12", "TİCKET 42"])
def test_unicode_regex_vocabulary_variants_do_not_crash_or_guess(surface):
    assert list(observations(surface)) == []


def test_supported_unicode_casefold_preserves_original_spans():
    text = "ſeptember 2026; deciſion; ticKet 42; März 2026"
    rows = list(observations(text))
    concepts = {concept for row in rows for concept in row["concepts"]}
    assert {"date:2026-09", "topic:decision", "entity:ticket:42", "date:2026-03"} <= concepts
    assert all(text[row["start"] : row["end"]] == row["quote"] for row in rows)


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("1.234,56", ["1234.56"]),
        ("1,234.56", ["1234.56"]),
        ("1’234.50", ["1234.5"]),
        ("1 234,50", ["1234.5"]),
        ("1\u202f234,50", ["1234.5"]),
        ("1.234", ["1.234", "1234"]),
        ("1,234", ["1.234", "1234"]),
        ("1.234.567", ["1234567"]),
        ("-123,45", ["-123.45"]),
        ("12'34", []),
        ("1,23,45", []),
        ("123456789012345678901234567890.01", ["123456789012345678901234567890.01"]),
    ],
)
def test_exact_amounts_and_separator_ambiguity(surface, expected):
    assert number_readings(surface) == expected


def test_currency_is_not_guessed_and_money_does_not_make_payment():
    text = "CHF 1’234.50 / 1.234,50 EUR / USD 1234.50 / $1234.50 / £1234.50"
    rows = list(money_mentions(text))
    assert len(rows) == 5
    assert [r["currency"] for r in rows] == ["CHF", "EUR", "USD", None, None]
    assert all(r["alternatives"] == ["1234.5"] for r in rows)
    assert all(text[r["start"] : r["end"]] == r["quote"] for r in rows)
    assert all(r["category"] == "money" for r in rows)


@pytest.mark.parametrize(
    "text,polarity,modality,day",
    [
        ("Alice approved order 1847 on 12 March 2026.", "positive", "asserted", "2026-03-12"),
        (
            "Özlem Müller hat die Bestellung 1847 am 12. März 2026 genehmigt.",
            "positive",
            "asserted",
            "2026-03-12",
        ),
        ("Die Bestellung 1847 wurde am 12. März 2026 nicht genehmigt.", "negative", "asserted", "2026-03-12"),
        ("Die Bestellung 1847 wurde am March 12, 2026 genehmigt.", "positive", "asserted", "2026-03-12"),
        ("Order 1847 was approved on 12. März 2026.", "positive", "asserted", "2026-03-12"),
        ("Die Bestellung 1847 muss nicht genehmigt werden.", "unresolved", "not_required", None),
        ("Order 1847 must not be approved.", "negative", "prohibited", None),
        ("Die Bestellung 1847 darf nicht genehmigt werden.", "negative", "prohibited", None),
        ("Die Bestellung 1847 kann nicht genehmigt werden.", "unresolved", "unresolved", None),
        ("Die Bestellung 1847 wird genehmigt.", "positive", "unresolved", None),
        ("Die Bestellung 1847 wird genehmigt werden.", "positive", "planned", None),
        ("Order 1847 was approved on 03/04/2026.", "positive", "asserted", None),
    ],
)
def test_bilingual_claims_and_scoped_modal_negation(text, polarity, modality, day):
    claim = parse_clause(text)
    assert claim is not None
    assert claim["entity_key"] == "order:1847"
    assert (claim["predicate"], claim["polarity"], claim["modality"], claim["applicable_on"]) == (
        "approval",
        polarity,
        modality,
        day,
    )


@pytest.mark.parametrize(
    "text",
    [
        "Niemand hat die Bestellung 1847 genehmigt.",
        "Die Bestellung 1847 wurde nicht nur genehmigt.",
        "Die Bestellung 1847 wurde noch nicht genehmigt.",
        "Die Bestellung 1847 wurde nicht mehr genehmigt.",
        "Die Bestellung 1847 soll genehmigt worden sein.",
        "Die Bestellung 1847 wurde nicht am 12. März 2026 genehmigt.",
        "Die Bestellung 1847 wurde genehmigt?",
        "Alice bestreitet, die Bestellung 1847 genehmigt zu haben.",
        "Die Bestellung 1847 wurde am 31. Februar 2026 genehmigt.",
        "La commande 1847 a été approuvée.",
    ],
)
def test_unsupported_grammar_does_not_promote_false_assertions(text):
    assert parse_clause(text) is None


def test_mixed_paragraph_produces_both_language_observations_without_translation():
    text = "Die Genehmigung für Bestellung 1847 erfolgte im März. Payment for invoice INV-12: CHF 500."
    rows = list(observations(text))
    concepts = {c for row in rows for c in row["concepts"]}
    assert {
        "month:3",
        "topic:approval",
        "topic:payment",
        "entity:order:1847",
        "entity:invoice:INV-12",
        "amount:CHF:500",
    } <= concepts
    assert all(text[row["start"] : row["end"]] == row["quote"] for row in rows)
