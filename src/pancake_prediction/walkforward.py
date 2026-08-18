from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

from .economics import PPM
from .replay import ReplaySnapshot


@dataclass(frozen=True, slots=True)
class OosSignal:
    epoch: int
    p_bull_ppm: int
    generated_at: int
    train_max_epoch: int
    fold: str | None = None


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold: int
    train_start_epoch: int
    train_end_epoch: int
    test_start_epoch: int
    test_end_epoch: int
    n_train: int
    n_test: int
    purge_rounds: int
    embargo_rounds: int


@dataclass(frozen=True, slots=True)
class OosMetrics:
    market: str
    n_scored: int
    n_ties_excluded: int
    n_missing_signal: int
    bull_base_rate: float | None
    brier_score: float | None
    brier_skill_score: float | None
    log_loss: float | None
    ece_10: float | None
    accuracy: float | None
    accuracy_ci95: tuple[float, float] | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def generate_expanding_folds(
    epochs: Iterable[int],
    *,
    min_train_rounds: int,
    test_rounds: int,
    purge_rounds: int = 2,
    embargo_rounds: int = 2,
) -> tuple[WalkForwardFold, ...]:
    ordered = sorted(set(int(epoch) for epoch in epochs))
    if min_train_rounds <= 0 or test_rounds <= 0:
        raise ValueError("min_train_rounds and test_rounds must be positive")
    if purge_rounds < 0 or embargo_rounds < 0:
        raise ValueError("purge_rounds and embargo_rounds must be non-negative")
    if len(ordered) < min_train_rounds + purge_rounds + 1:
        return ()

    folds: list[WalkForwardFold] = []
    test_start_index = min_train_rounds + purge_rounds
    fold_number = 0
    while test_start_index < len(ordered):
        train_end_index = test_start_index - purge_rounds - 1
        test_end_index = min(test_start_index + test_rounds - 1, len(ordered) - 1)
        folds.append(
            WalkForwardFold(
                fold=fold_number,
                train_start_epoch=ordered[0],
                train_end_epoch=ordered[train_end_index],
                test_start_epoch=ordered[test_start_index],
                test_end_epoch=ordered[test_end_index],
                n_train=train_end_index + 1,
                n_test=test_end_index - test_start_index + 1,
                purge_rounds=purge_rounds,
                embargo_rounds=embargo_rounds,
            )
        )
        fold_number += 1
        test_start_index = test_end_index + 1 + embargo_rounds
    return tuple(folds)


def validate_oos_provenance(signals: Iterable[OosSignal], *, purge_rounds: int = 2) -> None:
    if purge_rounds < 0:
        raise ValueError("purge_rounds must be non-negative")
    for signal in signals:
        if not 0 <= signal.p_bull_ppm <= PPM:
            raise ValueError("p_bull_ppm must be in [0, 1_000_000]")
        latest_allowed_train_epoch = signal.epoch - purge_rounds - 1
        if signal.train_max_epoch > latest_allowed_train_epoch:
            raise ValueError(
                "signal is not purged OOS: "
                f"epoch={signal.epoch}, train_max_epoch={signal.train_max_epoch}, "
                f"latest_allowed={latest_allowed_train_epoch}"
            )


def _wilson_interval(
    successes: int, n: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("n must be positive")
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _ece_10(points: list[tuple[float, int]]) -> float:
    bins: list[list[tuple[float, int]]] = [[] for _ in range(10)]
    for probability, outcome in points:
        bins[min(9, int(probability * 10.0))].append((probability, outcome))
    total = len(points)
    error = 0.0
    for bucket in bins:
        if not bucket:
            continue
        mean_p = sum(probability for probability, _outcome in bucket) / len(bucket)
        mean_y = sum(outcome for _probability, outcome in bucket) / len(bucket)
        error += len(bucket) / total * abs(mean_p - mean_y)
    return error


def evaluate_binary_oos(
    *,
    market: str,
    outcomes: Mapping[int, int],
    signals: Mapping[int, OosSignal],
    purge_rounds: int = 2,
    generated_at_floor: Mapping[int, int] | None = None,
    n_ties_excluded: int = 0,
) -> OosMetrics:
    """Score purged binary OOS predictions from an explicit epoch/outcome mapping."""

    if n_ties_excluded < 0:
        raise ValueError("n_ties_excluded must be non-negative")
    validate_oos_provenance(signals.values(), purge_rounds=purge_rounds)
    points: list[tuple[float, int]] = []
    missing = 0
    for epoch in sorted(outcomes):
        outcome = outcomes[epoch]
        if outcome not in (0, 1):
            raise ValueError(f"outcome for epoch {epoch} must be binary 0/1")
        signal = signals.get(epoch)
        if signal is None:
            missing += 1
            continue
        if signal.epoch != epoch:
            raise ValueError(f"signal map key/epoch mismatch at epoch {epoch}")
        if generated_at_floor is not None:
            floor = generated_at_floor.get(epoch)
            if floor is None:
                raise ValueError(f"missing generated_at floor for epoch {epoch}")
            if signal.generated_at < floor:
                raise ValueError(f"signal for epoch {epoch} predates allowed observation window")
        points.append((signal.p_bull_ppm / PPM, outcome))

    if not points:
        return OosMetrics(
            market=market,
            n_scored=0,
            n_ties_excluded=n_ties_excluded,
            n_missing_signal=missing,
            bull_base_rate=None,
            brier_score=None,
            brier_skill_score=None,
            log_loss=None,
            ece_10=None,
            accuracy=None,
            accuracy_ci95=None,
        )

    n = len(points)
    base_rate = sum(outcome for _probability, outcome in points) / n
    brier = sum((probability - outcome) ** 2 for probability, outcome in points) / n
    base_brier = sum((base_rate - outcome) ** 2 for _probability, outcome in points) / n
    skill = None if base_brier == 0.0 else 1.0 - brier / base_brier
    epsilon = 1e-12
    log_loss = 0.0
    successes = 0
    for probability, outcome in points:
        p = min(1.0 - epsilon, max(epsilon, probability))
        log_loss -= outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p)
        successes += int((probability >= 0.5) == bool(outcome))
    return OosMetrics(
        market=market,
        n_scored=n,
        n_ties_excluded=n_ties_excluded,
        n_missing_signal=missing,
        bull_base_rate=base_rate,
        brier_score=brier,
        brier_skill_score=skill,
        log_loss=log_loss / n,
        ece_10=_ece_10(points),
        accuracy=successes / n,
        accuracy_ci95=_wilson_interval(successes, n),
    )


def evaluate_oos(
    replay: ReplaySnapshot,
    signals: dict[int, OosSignal],
    *,
    purge_rounds: int = 2,
) -> OosMetrics:
    outcomes: dict[int, int] = {}
    floors: dict[int, int] = {}
    ties = 0
    for record in replay.rounds:
        if record.label == "unresolved":
            continue
        if record.label == "tie":
            ties += 1
            continue
        outcomes[record.epoch] = 1 if record.label == "bull" else 0
        if record.start_timestamp is not None:
            floors[record.epoch] = record.start_timestamp
    floor_map: Mapping[int, int] | None = floors if len(floors) == len(outcomes) else None
    return evaluate_binary_oos(
        market=replay.market,
        outcomes=outcomes,
        signals=signals,
        purge_rounds=purge_rounds,
        generated_at_floor=floor_map,
        n_ties_excluded=ties,
    )
