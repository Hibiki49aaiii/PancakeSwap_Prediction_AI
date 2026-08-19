from __future__ import annotations

import math

import pytest

from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.metrics import (
    OutcomeProbability,
    evaluate_outcome_probabilities,
    evaluate_probabilities,
)


def test_perfect_binary_probabilities_have_zero_brier_and_ece() -> None:
    metrics = evaluate_probabilities([0.0, 1.0], [0, 1], n_bins=2)
    assert metrics.brier_score == 0.0
    assert metrics.expected_calibration_error == 0.0
    assert metrics.accuracy_at_half == 1.0
    assert metrics.log_loss < 1e-12


def test_known_binary_brier_score() -> None:
    metrics = evaluate_probabilities([0.8, 0.2], [1, 0], n_bins=5)
    assert math.isclose(metrics.brier_score, 0.04)
    assert metrics.accuracy_at_half == 1.0


def test_multiclass_metrics_preserve_tie_class() -> None:
    metrics = evaluate_outcome_probabilities(
        [
            OutcomeProbability(0.90, 0.09, 0.01),
            OutcomeProbability(0.10, 0.85, 0.05),
            OutcomeProbability(0.10, 0.10, 0.80),
        ],
        [Outcome.BULL, Outcome.BEAR, Outcome.TIE],
        n_bins=5,
    )
    assert metrics.top_label_accuracy == 1.0
    assert metrics.tie_rate == pytest.approx(1 / 3)
    assert metrics.multiclass_brier_score < 0.03
    assert metrics.log_loss < 0.2


def test_multiclass_probability_mass_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        evaluate_outcome_probabilities(
            [OutcomeProbability(0.5, 0.5, 0.1)],
            [Outcome.BULL],
        )


def test_invalid_binary_probability_rejected() -> None:
    with pytest.raises(ValueError, match="probabilities"):
        evaluate_probabilities([1.1], [1])


def test_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        evaluate_probabilities([0.5, 0.6], [1])
