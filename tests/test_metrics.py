from __future__ import annotations

import math

import pytest

from pancake_prediction_ai.metrics import evaluate_probabilities


def test_perfect_probabilities_have_zero_brier_and_ece() -> None:
    metrics = evaluate_probabilities([0.0, 1.0], [0, 1], n_bins=2)
    assert metrics.brier_score == 0.0
    assert metrics.expected_calibration_error == 0.0
    assert metrics.accuracy_at_half == 1.0
    assert metrics.log_loss < 1e-12


def test_known_brier_score() -> None:
    metrics = evaluate_probabilities([0.8, 0.2], [1, 0], n_bins=5)
    assert math.isclose(metrics.brier_score, 0.04)
    assert metrics.accuracy_at_half == 1.0


def test_invalid_probability_rejected() -> None:
    with pytest.raises(ValueError, match="probabilities"):
        evaluate_probabilities([1.1], [1])


def test_length_mismatch_rejected() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        evaluate_probabilities([0.5, 0.6], [1])
