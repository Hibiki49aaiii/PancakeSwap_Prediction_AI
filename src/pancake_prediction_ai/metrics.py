from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .economics import Outcome


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


@dataclass(frozen=True, slots=True)
class OutcomeProbability:
    bull: float
    bear: float
    tie: float

    def validate(self) -> None:
        values = (self.bull, self.bear, self.tie)
        if any(not 0.0 <= value <= 1.0 for value in values):
            raise ValueError("outcome probabilities must be in [0, 1]")
        if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("outcome probabilities must sum to 1")

    @property
    def predicted(self) -> Outcome:
        scores = {
            Outcome.BULL: self.bull,
            Outcome.BEAR: self.bear,
            Outcome.TIE: self.tie,
        }
        return max(scores, key=scores.__getitem__)

    def probability_of(self, outcome: Outcome) -> float:
        if outcome is Outcome.BULL:
            return self.bull
        if outcome is Outcome.BEAR:
            return self.bear
        return self.tie


@dataclass(frozen=True, slots=True)
class OutcomeMetrics:
    count: int
    multiclass_brier_score: float
    log_loss: float
    top_label_accuracy: float
    expected_calibration_error: float
    tie_rate: float
    calibration_bins: tuple[CalibrationBin, ...]


def _calibration_bins(
    confidences_and_correct: Iterable[tuple[float, int]],
    *,
    n_bins: int,
) -> tuple[tuple[CalibrationBin, ...], float]:
    pairs = tuple(confidences_and_correct)
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for confidence, correct in pairs:
        index = min(n_bins - 1, int(confidence * n_bins))
        buckets[index].append((confidence, correct))

    calibration: list[CalibrationBin] = []
    weighted_gap = 0.0
    n = len(pairs)
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
    return tuple(calibration), weighted_gap


def evaluate_probabilities(
    probabilities: Iterable[float],
    outcomes: Iterable[int | bool],
    *,
    n_bins: int = 10,
    epsilon: float = 1e-15,
) -> ProbabilityMetrics:
    """Binary one-vs-rest diagnostics retained for individual class analysis."""

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
    calibration, ece = _calibration_bins(zip(probs, ys, strict=True), n_bins=n_bins)

    return ProbabilityMetrics(
        count=n,
        brier_score=brier,
        log_loss=log_loss,
        accuracy_at_half=accuracy,
        expected_calibration_error=ece,
        calibration_bins=calibration,
    )


def evaluate_outcome_probabilities(
    probabilities: Iterable[OutcomeProbability],
    outcomes: Iterable[Outcome],
    *,
    n_bins: int = 10,
    epsilon: float = 1e-15,
) -> OutcomeMetrics:
    probs = tuple(probabilities)
    ys = tuple(outcomes)
    if len(probs) != len(ys):
        raise ValueError("probabilities/outcomes length mismatch")
    if not probs:
        raise ValueError("at least one observation is required")
    if n_bins <= 0:
        raise ValueError("n_bins must be positive")
    if not 0.0 < epsilon < 0.5:
        raise ValueError("epsilon must be in (0, 0.5)")
    for probability in probs:
        probability.validate()

    brier_total = 0.0
    log_loss_total = 0.0
    correct = 0
    tie_count = 0
    calibration_pairs: list[tuple[float, int]] = []
    for probability, outcome in zip(probs, ys, strict=True):
        target = (
            1.0 if outcome is Outcome.BULL else 0.0,
            1.0 if outcome is Outcome.BEAR else 0.0,
            1.0 if outcome is Outcome.TIE else 0.0,
        )
        vector = (probability.bull, probability.bear, probability.tie)
        brier_total += sum((p - y) ** 2 for p, y in zip(vector, target, strict=True)) / 3.0
        true_probability = max(epsilon, probability.probability_of(outcome))
        log_loss_total += -math.log(true_probability)
        predicted = probability.predicted
        is_correct = int(predicted is outcome)
        correct += is_correct
        confidence = max(vector)
        calibration_pairs.append((confidence, is_correct))
        if outcome is Outcome.TIE:
            tie_count += 1

    n = len(probs)
    calibration, ece = _calibration_bins(calibration_pairs, n_bins=n_bins)
    return OutcomeMetrics(
        count=n,
        multiclass_brier_score=brier_total / n,
        log_loss=log_loss_total / n,
        top_label_accuracy=correct / n,
        expected_calibration_error=ece,
        tie_rate=tie_count / n,
        calibration_bins=calibration,
    )
