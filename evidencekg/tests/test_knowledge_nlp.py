"""Optional real conventional parsers; deterministic base operation needs none."""

import pytest

from evidencekg import knowledge_nlp as nlp


def test_absent_optional_models_are_an_explicit_gap(monkeypatch):
    monkeypatch.setattr(
        nlp,
        "identity",
        lambda: {"models": {language: {"status": "unavailable"} for language in ("en", "de")}},
    )
    result = nlp.analyse("Die Bestellung wurde genehmigt. The order was approved.")
    assert result == {"mentions": [], "sentences": [], "gaps": {"optional_nlp_models_unavailable": 1}}


@pytest.fixture
def real_models():
    if any(row["status"] != "available" for row in nlp.identity()["models"].values()):
        pytest.skip("Install the locked knowledge-nlp extra for real EN/DE CNN checks")


def test_real_bilingual_models_preserve_disagreement_and_exact_spans(real_models):
    text = "Alice founded Example Ltd. Özlem Müller gründete Beispiel AG im März."
    items, gaps = nlp.observations(text)
    assert not gaps
    assert any(row["category"] == "name_candidate" for row in items)
    syntax = [row for row in items if row["category"] == "syntax_candidate"]
    assert syntax
    languages = {prediction["language"] for row in syntax for prediction in row["syntax_candidates"]}
    assert languages == {"en", "de"}
    for row in items:
        assert text[row["start"] : row["end"]] == row["quote"]
        assert row["interpretation_status"] == "model_prediction"
    for row in syntax:
        assert row["polarity"] == row["modality"] == row["attribution"] == "unresolved"
        assert row["calibrated_probability"] is None
        for prediction in row["syntax_candidates"]:
            for token in [prediction["predicate_surface"], *prediction["dependents"]]:
                assert text[token["start"] : token["end"]] == token["text"]
    # Hints are relative to two permitted languages, not proof that arbitrary
    # third-language text belongs to either language.
    assert nlp.identity()["generative_model_calls"] == 0
    assert all("transformer" not in nlp.pipeline(language).pipe_names for language in ("en", "de"))


def test_real_nlp_repeatability_and_visible_budgets(real_models, monkeypatch):
    text = "Alice approved order 1847. Özlem Müller hat die Bestellung 1848 genehmigt."
    expected = nlp.observations(text)
    for _ in range(10):
        assert nlp.observations(text) == expected
    monkeypatch.setattr(nlp, "MAX_MENTIONS", 0)
    monkeypatch.setattr(nlp, "MAX_SENTENCES", 0)
    result = nlp.analyse(text)
    assert result["mentions"] == result["sentences"] == []
    assert result["gaps"]["nlp_mention_limit"] > 0
    assert result["gaps"]["nlp_sentence_limit"] > 0
