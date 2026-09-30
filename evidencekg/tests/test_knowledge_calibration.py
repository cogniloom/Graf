import copy

import pytest

from evidencekg.knowledge_calibration import apply, fit


def references():
    scope = dict(
        model_sha256="a" * 64,
        language="de",
        input_domain="synthetic-test-only",
        probability_target="transcription_fidelity",
    )
    training = [
        dict(raw_score=score, correct=correct, source_family=f"train-{i}")
        for i, (score, correct) in enumerate(((0.1, False), (0.2, False), (0.8, True), (0.9, True)))
    ]
    validation = [
        dict(raw_score=score, correct=correct, source_family=f"heldout-{i}")
        for i, (score, correct) in enumerate(((0.15, False), (0.85, True)))
    ]
    return training, validation, scope


def test_independent_reference_fit_and_scope_abstention():
    train, validation, scope = references()
    model = fit(train, validation, scope)
    estimate = apply(model, 0.85, scope)
    assert estimate["calibrated_probability"] == 1
    assert estimate["claim_truth"] is None
    assert estimate["validation"]["evaluated"] == 2
    for incompatible in (
        scope | {"language": "en"},
        scope | {"input_domain": "noisy-field-audio"},
        scope | {"model_sha256": "b" * 64},
    ):
        assert apply(model, 0.85, incompatible)["calibrated_probability"] is None
    assert apply(model, 0.99, scope)["calibrated_probability"] is None
    assert apply(model, None, scope)["calibrated_probability"] is None


def test_rejects_family_leakage_and_unsupported_probability_target():
    train, validation, scope = references()
    validation[0]["source_family"] = train[0]["source_family"]
    with pytest.raises(ValueError, match="disjoint"):
        fit(train, validation, scope)
    with pytest.raises(ValueError, match="fidelity"):
        fit(train, validation, scope | {"probability_target": "claim_truth"})


def test_calibration_that_worsens_heldout_brier_is_not_applied():
    train, validation, scope = references()
    reversed_validation = copy.deepcopy(validation)
    for row in reversed_validation:
        row["correct"] = not row["correct"]
    model = fit(train, reversed_validation, scope)
    assert apply(model, 0.85, scope)["calibrated_probability"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m["blocks"][0].update(correct=100),
        lambda m: m["blocks"][0].update(count=0),
        lambda m: m["blocks"][0].update(upper_score=float("nan")),
        lambda m: m["validation"].update(raw_brier=float("nan")),
        lambda m: m.update(score_range=[-1, 2]),
    ],
)
def test_malformed_calibration_cannot_emit_a_probability(mutation):
    train, validation, scope = references()
    model = fit(train, validation, scope)
    mutation(model)
    with pytest.raises(ValueError, match="calibration artifact"):
        apply(model, 0.85, scope)
