from __future__ import annotations

import pytest

from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.tie_prior import TiePriorPolicy, estimate_tie_prior, wilson_upper_bound


def test_zero_observed_ties_still_produces_nonzero_conservative_probability() -> None:
    estimate = estimate_tie_prior(
        [Outcome.BULL] * 50 + [Outcome.BEAR] * 50,
        policy=TiePriorPolicy(z_score=2.3263478740408408),
    )
    assert estimate.tie_count == 0
    assert estimate.empirical_tie_rate == 0.0
    assert estimate.wilson_tie_upper > 0.0
    assert estimate.probability.tie == pytest.approx(estimate.wilson_tie_upper)
    assert estimate.probability.bull == pytest.approx(estimate.probability.bear)
    assert estimate.probability.bull + estimate.probability.bear + estimate.probability.tie == pytest.approx(1.0)


def test_more_history_without_ties_tightens_wilson_upper_bound() -> None:
    short = wilson_upper_bound(0, 100, z_score=2.3263478740408408)
    long = wilson_upper_bound(0, 1000, z_score=2.3263478740408408)
    assert 0 < long < short < 1


def test_directional_split_uses_only_training_outcomes() -> None:
    estimate = estimate_tie_prior(
        [Outcome.BULL] * 80 + [Outcome.BEAR] * 20,
        policy=TiePriorPolicy(directional_alpha=0.5),
    )
    assert estimate.probability.bull > estimate.probability.bear
    assert estimate.sample_count == 100
    assert estimate.bull_count == 80
    assert estimate.bear_count == 20


def test_all_tie_history_cannot_define_directional_prior() -> None:
    with pytest.raises(ValueError, match="directional"):
        estimate_tie_prior([Outcome.TIE, Outcome.TIE])
