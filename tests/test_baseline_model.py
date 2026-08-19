from __future__ import annotations

import pytest

from pancake_prediction_ai.baseline_model import (
    LabeledFeatures,
    fit_softmax_baseline,
    fit_temperature,
    mean_log_loss,
)
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.metrics import OutcomeProbability


def _examples() -> list[LabeledFeatures]:
    return [
        LabeledFeatures({"x": -3.0}, Outcome.BEAR),
        LabeledFeatures({"x": -2.5}, Outcome.BEAR),
        LabeledFeatures({"x": -2.0}, Outcome.BEAR),
        LabeledFeatures({"x": -0.2}, Outcome.TIE),
        LabeledFeatures({"x": 0.0}, Outcome.TIE),
        LabeledFeatures({"x": 0.2}, Outcome.TIE),
        LabeledFeatures({"x": 2.0}, Outcome.BULL),
        LabeledFeatures({"x": 2.5}, Outcome.BULL),
        LabeledFeatures({"x": 3.0}, Outcome.BULL),
    ]


def test_softmax_baseline_learns_all_three_classes() -> None:
    model = fit_softmax_baseline(
        _examples(),
        feature_names=["x"],
        learning_rate=0.1,
        epochs=1200,
        l2=1e-4,
    )
    bear = model.predict({"x": -2.7})
    tie = model.predict({"x": 0.0})
    bull = model.predict({"x": 2.7})
    assert bear.predicted is Outcome.BEAR
    assert tie.predicted is Outcome.TIE
    assert bull.predicted is Outcome.BULL
    assert bear.bear > bear.bull and bear.bear > bear.tie
    assert tie.tie > tie.bull and tie.tie > tie.bear
    assert bull.bull > bull.bear and bull.bull > bull.tie


def test_training_is_deterministic_and_model_artifact_is_hashable() -> None:
    first = fit_softmax_baseline(_examples(), feature_names=["x"], epochs=200)
    second = fit_softmax_baseline(_examples(), feature_names=["x"], epochs=200)
    assert first.weights == second.weights
    assert first.artifact_sha256 == second.artifact_sha256
    assert len(first.artifact_sha256) == 64


def test_temperature_calibration_does_not_worsen_calibration_set_log_loss() -> None:
    model = fit_softmax_baseline(_examples(), feature_names=["x"], learning_rate=0.1, epochs=400)
    before = mean_log_loss(model, _examples())
    calibrated = fit_temperature(model, _examples(), lower=0.25, upper=4.0, iterations=60)
    after = mean_log_loss(calibrated, _examples())
    assert calibrated.temperature > 0
    assert after <= before + 1e-9


def test_missing_tie_without_prior_is_rejected_instead_of_binary_collapse() -> None:
    examples = [
        LabeledFeatures({"x": -1.0}, Outcome.BEAR),
        LabeledFeatures({"x": 1.0}, Outcome.BULL),
    ]
    with pytest.raises(ValueError, match="explicit three-outcome class_prior"):
        fit_softmax_baseline(examples, feature_names=["x"])


def test_missing_tie_can_train_only_with_explicit_three_outcome_prior() -> None:
    examples = [
        LabeledFeatures({"x": -2.0}, Outcome.BEAR),
        LabeledFeatures({"x": -1.0}, Outcome.BEAR),
        LabeledFeatures({"x": 1.0}, Outcome.BULL),
        LabeledFeatures({"x": 2.0}, Outcome.BULL),
    ]
    prior = OutcomeProbability(bull=0.495, bear=0.495, tie=0.01)
    model = fit_softmax_baseline(
        examples,
        feature_names=["x"],
        class_prior=prior,
        prior_strength=20.0,
        epochs=500,
    )
    neutral = model.predict({"x": 0.0})
    assert neutral.tie > 0
    assert model.training_prior == prior
    assert model.prior_strength == 20.0
    assert model.artifact_payload()["training_prior"] == {
        "bull": 0.495,
        "bear": 0.495,
        "tie": 0.01,
        "strength": 20.0,
    }


def test_prior_strength_without_prior_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires class_prior"):
        fit_softmax_baseline(_examples(), feature_names=["x"], prior_strength=1.0)


def test_missing_prediction_feature_is_hard_failure() -> None:
    model = fit_softmax_baseline(_examples(), feature_names=["x"], epochs=10)
    with pytest.raises(ValueError, match="missing model feature"):
        model.predict({"other": 1.0})
