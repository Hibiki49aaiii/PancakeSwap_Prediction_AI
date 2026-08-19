from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_probability: float
    empirical_rate: float
    absolute_gap: float


@dataclass(frozen=True, slots=True)
class ProbabilityMetrics:
    count: int
    brier_score: float
    log_loss: float
    accuracy_at_half: float
    expected_calibration_error: float
    calibration_bins: tuple[CalibrationBin, ...]


def evaluate_probabilities(
    probabilities: Iterable[float],
    outcomes: Iterable[int | bool],
    *,
    n_bins: int = 10,
    epsilon: float = 1e-15,
) -> ProbabilityMetrics:
    probs = tuple(float(p) for p in probabilities)
    ys = tuple(int(y) for y in outcomes)
    if len(probs) != len(ys):
        raise ValueError("probabilities/outcomes length mismatch")
    if not probs:
        raise ValueError("at least one observation is required")
    if n_bins <= 0:
        raise ValueError("n_bins must be positive")
    if not 0.0 < epsilon < 0.5:
        raise ValueError("epsilon must be in (0, 0.5)")
    if any(not 0.0 <= p <= 1.0 for p in probs):
        raise ValueError("probabilities must be in [0, 1]")
    if any(y not in {0, 1} for y in ys):
        raise ValueError("outcomes must be binary")

    n = len(probs)
    brier = sum((p - y) ** 2 for p, y in zip(probs, ys, strict=True)) / n
    clipped = tuple(min(1.0 - epsilon, max(epsilon, p)) for p in probs)
    log_loss = -sum(
        y * math.log(p) + (1 - y) * math.log(1.0 - p)
        for p, y in zip(clipped, ys, strict=True)
    ) / n
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(probs, ys, strict=True)) / n

    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, ys, strict=True):
        index = min(n_bins - 1, int(p * n_bins))
        buckets[index].append((p, y))

    calibration: list[CalibrationBin] = []
    weighted_gap = 0.0
    for index, bucket in enumerate(buckets):
        lower = index / n_bins
        upper = (index + 1) / n_bins
        if not bucket:
            calibration.append(CalibrationBin(lower, upper, 0, 0.0, 0.0, 0.0))
            continue
        count = len(bucket)
        mean_probability = sum(p for p, _ in bucket) / count
        empirical_rate = sum(y for _, y in bucket) / count
        gap = abs(mean_probability - empirical_rate)
        weighted_gap += count / n * gap
        calibration.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=count,
                mean_probability=mean_probability,
                empirical_rate=empirical_rate,
                absolute_gap=gap,
            )
        )

    return ProbabilityMetrics(
        count=n,
        brier_score=brier,
        log_loss=log_loss,
        accuracy_at_half=accuracy,
        expected_calibration_error=weighted_gap,
        calibration_bins=tuple(calibration),
    )
