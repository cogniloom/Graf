import copy

import pytest

from evidencekg.investigations.accuracy_eval import digest, report


def cohort():
    corpus = {
        "cases": [
            {"case_id": "one", "sources": ["The order was not approved."]},
            {"case_id": "two", "sources": ["No attachment."]},
        ]
    }
    observation = {
        "case_id": "one",
        "source_sha": digest(corpus["cases"][0]["sources"]),
        "status": "ok",
        "graph_claims": [{"polarity": "negative"}],
        "answer": "Denied.",
    }
    predictions = {
        "corpus_sha": digest(corpus),
        "implementation": "test",
        "prompt_version": "test",
        "model": "test",
        "effort": "test",
        "cases": [observation],
    }
    labels = {
        "corpus_sha": digest(corpus),
        "cases": [
            {
                "case_id": "one",
                "reviewer": "fixture-reviewer",
                "source_sha": observation["source_sha"],
                "observation_sha": digest(observation),
                "graph_expected": [{"polarity": "negative"}],
                "qualifications": {"negation": True},
                "answer_ratings": {"supported_conclusions": True},
            }
        ],
    }
    return corpus, predictions, labels


def test_missing_is_unknown_and_metrics_have_explicit_denominators():
    result = report(*cohort())
    assert result["totals"] == dict(scheduled=2, recorded=1, failed=0, missing=1, reviewed=1, unreviewed=1)
    assert result["graph"]["precision"] == 1
    assert result["answers"]["supported_conclusions"] == dict(passed=1, assessed=1, unknown=1)
    assert result["answers"]["contradiction_coverage"]["unknown"] == 2


def test_unreviewed_seed_does_not_become_accuracy():
    corpus, predictions, labels = cohort()
    labels["cases"][0]["reviewer"] = ""
    result = report(corpus, predictions, labels)
    assert result["totals"]["reviewed"] == 0
    assert result["graph"]["precision"] is None


def test_reject_duplicates_foreign_changed_sources_and_stale_reviews():
    for mutation in [
        lambda p: p["cases"].append(copy.deepcopy(p["cases"][0])),
        lambda p: p["cases"][0].update(case_id="foreign"),
        lambda p: p["cases"][0].update(source_sha="wrong"),
        lambda p: p["cases"][0].update(answer="changed"),
    ]:
        corpus, predictions, labels = cohort()
        mutation(predictions)
        with pytest.raises(ValueError):
            report(corpus, predictions, labels)


def test_failures_and_non_boolean_ratings():
    corpus, predictions, labels = cohort()
    predictions["cases"][0].update(status="error", error="Extraction failed")
    result = report(corpus, predictions, labels)
    assert result["totals"]["failed"] == 1
    assert result["cases"][0]["error"] == "Extraction failed"
    corpus, predictions, labels = cohort()
    labels["cases"][0]["answer_ratings"]["supported_conclusions"] = "yes"
    with pytest.raises(ValueError):
        report(corpus, predictions, labels)
