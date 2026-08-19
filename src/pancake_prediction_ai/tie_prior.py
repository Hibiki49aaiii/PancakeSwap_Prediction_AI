from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .economics import Outcome
from .metrics import OutcomeProbability


@dataclass(frozen=True, slots=True)
class TiePriorPolicy:
    """Train-only conservative prior policy for rare house-win TIE outcomes.

    `z_score` controls the Wilson upper confidence bound for the historical tie
    rate. `directional_alpha` applies symmetric additive smoothing only to the
    BULL/BEAR split after reserving the conservative TIE mass.
    """

    z_score: float = 2.3263478740408408  # one-sided 99% standard-normal quantile
    directional_alpha: float = 0.5
    minimum_tie_probability: float = 1e-6

    def validate(self) -> None:
        if self.z_score <= 0 or not math.isfinite(self.z_score):
            raise ValueError("z_score must be positive finite")
        if self.directional_alpha < 0 or not math.isfinite(self.directional_alpha):
            raise ValueError("directional_alpha must be non-negative finite")
        if not 0.0 <= self.minimum_tie_probability < 1.0:
            raise ValueError("minimum_tie_probability must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class TiePriorEstimate:
    sample_count: int
    bull_count: int
    bear_count: int
    tie_count: int
    empirical_tie_rate: float
    wilson_tie_upper: float
    probability: OutcomeProbability
    policy: TiePriorPolicy


def wilson_upper_bound(successes: int, trials: int, *, z_score: float) -> float:
    if trials <= 0:
        raise ValueError("trials must be positive")
    if successes < 0 or successes > trials:
        raise ValueError("successes must be in [0, trials]")
    if z_score <= 0 or not math.isfinite(z_score):
        raise ValueError("z_score must be positive finite")

    p = successes / trials
    z2 = z_score * z_score
    denominator = 1.0 + z2 / trials
    center = (p + z2 / (2.0 * trials)) / denominator
    radius = (
        z_score
        * math.sqrt(p * (1.0 - p) / trials + z2 / (4.0 * trials * trials))
        / denominator
    )
    return min(1.0, center + radius)


def estimate_tie_prior(
    outcomes: Iterable[Outcome],
    *,
    policy: TiePriorPolicy = TiePriorPolicy(),
) -> TiePriorEstimate:
    policy.validate()
    values = tuple(outcomes)
    if not values:
        raise ValueError("outcomes are required")
    bull = sum(value is Outcome.BULL for value in values)
    bear = sum(value is Outcome.BEAR for value in values)
    tie = sum(value is Outcome.TIE for value in values)
    n = len(values)
    if bull + bear == 0:
        raise ValueError("at least one directional outcome is required")

    upper = wilson_upper_bound(tie, n, z_score=policy.z_score)
    tie_probability = max(policy.minimum_tie_probability, upper)
    tie_probability = min(tie_probability, 1.0 - 1e-12)

    bull_smoothed = bull + policy.directional_alpha
    bear_smoothed = bear + policy.directional_alpha
    directional_total = bull_smoothed + bear_smoothed
    if directional_total <= 0:
        raise ValueError("directional prior denominator must be positive")
    remaining = 1.0 - tie_probability
    probability = OutcomeProbability(
        bull=remaining * bull_smoothed / directional_total,
        bear=remaining * bear_smoothed / directional_total,
        tie=tie_probability,
    )
    probability.validate()
    return TiePriorEstimate(
        sample_count=n,
        bull_count=bull,
        bear_count=bear,
        tie_count=tie,
        empirical_tie_rate=tie / n,
        wilson_tie_upper=upper,
        probability=probability,
        policy=policy,
    )
