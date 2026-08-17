from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .alpha import AlphaFeatureRow
from .calibration import (
    CalibrationPoint,
    HistogramCalibrator,
    fit_histogram_calibrator,
)
from .features import PoolFeatureRow
from .replay import ReplaySnapshot
from .walkforward import OosMetrics, OosSignal, evaluate_oos, generate_expanding_folds

PPM = 1_000_000

FEATURE_FAMILIES: dict[str, tuple[str, ...]] = {
    "settlement_source": (
        "oracle_age_ms",
        "spot_oracle_gap_ppm",
        "perp_oracle_gap_ppm",
        "spot_perp_basis_ppm",
        "oracle_update_hazard_ppm",
    ),
    "cex_flow": (
        "spot_flow_imbalance_ppm",
        "perp_flow_imbalance_ppm",
    ),
    "pool_state": (
        "pool_bull_share_ppm",
        "pool_log_total_bnb",
        "pool_recent_flow_imbalance_ppm",
        "pool_log_bet_count",
        "pool_log_unique_bettors",
    ),
    "round_history": (
        "prior_bull_rate_20_ppm",
        "prior_abs_return_12_ppm",
    ),
}

ALL_FEATURE_NAMES = tuple(
    name for names in FEATURE_FAMILIES.values() for name in names
)


@dataclass(frozen=True, slots=True)
class ResearchFeatureRow:
    market: str
    epoch: int
    decision_timestamp_ms: int
    values: Mapping[str, float | None]

    def selected(self, feature_names: Sequence[str]) -> tuple[float | None, ...]:
        return tuple(self.values.get(name) for name in feature_names)


@dataclass(frozen=True, slots=True)
class StandardizedLogisticModel:
    feature_names: tuple[str, ...]
    means: tuple[float, ...]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    intercept: float
    train_max_epoch: int
    model_id: str

    def predict_ppm(self, row: ResearchFeatureRow) -> int:
        values = row.selected(self.feature_names)
        score = self.intercept
        for value, mean, scale, weight in zip(
            values, self.means, self.scales, self.weights, strict=True
        ):
            standardized = 0.0 if value is None else (float(value) - mean) / scale
            score += weight * standardized
        probability = _sigmoid(score)
        return max(0, min(PPM, int(round(probability * PPM))))


@dataclass(frozen=True, slots=True)
class WalkForwardBaselineResult:
    feature_set_id: str
    feature_names: tuple[str, ...]
    signals: Mapping[int, OosSignal]
    metrics: OosMetrics
    fold_count: int
    calibration_failures: int


@dataclass(frozen=True, slots=True)
class AblationResult:
    feature_set_id: str
    removed_family: str | None
    n_features: int
    n_scored: int
    brier_score: float | None
    brier_skill_score: float | None
    log_loss: float | None
    ece_10: float | None
    accuracy: float | None


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        exp_neg = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + exp_neg)
    exp_pos = math.exp(max(value, -60.0))
    return exp_pos / (1.0 + exp_pos)


def _recent_flow_imbalance_ppm(pool: PoolFeatureRow) -> float | None:
    total = pool.last_60s_bull_wei + pool.last_60s_bear_wei
    if total <= 0:
        return None
    return (pool.last_60s_bull_wei - pool.last_60s_bear_wei) * PPM / total


def build_research_feature_row(
    *,
    alpha: AlphaFeatureRow,
    pool: PoolFeatureRow,
) -> ResearchFeatureRow:
    if alpha.market != pool.market or alpha.epoch != pool.epoch:
        raise ValueError("alpha and pool rows must refer to the same market/epoch")
    pool_timestamp_ms = pool.feature_timestamp * 1_000
    if alpha.decision_timestamp_ms != pool_timestamp_ms:
        raise ValueError("alpha and pool rows must use the same decision cutoff")
    total_bnb = (pool.bull_pool_wei + pool.bear_pool_wei) / 10**18
    values: dict[str, float | None] = {
        "oracle_age_ms": float(alpha.oracle_age_ms),
        "spot_oracle_gap_ppm": float(alpha.spot_oracle_gap_ppm),
        "perp_oracle_gap_ppm": _float_or_none(alpha.perp_oracle_gap_ppm),
        "spot_perp_basis_ppm": _float_or_none(alpha.spot_perp_basis_ppm),
        "oracle_update_hazard_ppm": _float_or_none(alpha.oracle_update_hazard_ppm),
        "spot_flow_imbalance_ppm": _float_or_none(alpha.spot_flow_imbalance_ppm),
        "perp_flow_imbalance_ppm": _float_or_none(alpha.perp_flow_imbalance_ppm),
        "pool_bull_share_ppm": _float_or_none(pool.bull_share_ppm),
        "pool_log_total_bnb": math.log1p(total_bnb),
        "pool_recent_flow_imbalance_ppm": _recent_flow_imbalance_ppm(pool),
        "pool_log_bet_count": math.log1p(pool.bet_count),
        "pool_log_unique_bettors": math.log1p(pool.unique_bettors),
        "prior_bull_rate_20_ppm": _float_or_none(pool.prior_bull_rate_20_ppm),
        "prior_abs_return_12_ppm": _float_or_none(pool.prior_abs_return_12_ppm),
    }
    return ResearchFeatureRow(
        market=alpha.market,
        epoch=alpha.epoch,
        decision_timestamp_ms=alpha.decision_timestamp_ms,
        values=values,
    )


def _float_or_none(value: int | None) -> float | None:
    return None if value is None else float(value)


def feature_names_without_family(removed_family: str | None) -> tuple[str, ...]:
    if removed_family is not None and removed_family not in FEATURE_FAMILIES:
        raise ValueError(f"unknown feature family: {removed_family}")
    return tuple(
        name
        for family, names in FEATURE_FAMILIES.items()
        if family != removed_family
        for name in names
    )


def _training_stats(
    rows: Sequence[ResearchFeatureRow], feature_names: tuple[str, ...]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    means: list[float] = []
    scales: list[float] = []
    for name in feature_names:
        observed: list[float] = []
        for row in rows:
            value = row.values.get(name)
            if value is not None:
                observed.append(float(value))
        if not observed:
            means.append(0.0)
            scales.append(1.0)
            continue
        mean = sum(observed) / len(observed)
        variance = sum((value - mean) ** 2 for value in observed) / len(observed)
        means.append(mean)
        scales.append(max(math.sqrt(variance), 1e-9))
    return tuple(means), tuple(scales)


def _standardized_vector(
    row: ResearchFeatureRow,
    feature_names: tuple[str, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> tuple[float, ...]:
    result: list[float] = []
    for name, mean, scale in zip(feature_names, means, scales, strict=True):
        value = row.values.get(name)
        result.append(0.0 if value is None else (float(value) - mean) / scale)
    return tuple(result)


def fit_logistic_baseline(
    rows: Sequence[ResearchFeatureRow],
    outcomes: Mapping[int, int],
    *,
    feature_names: tuple[str, ...] = ALL_FEATURE_NAMES,
    learning_rate: float = 0.05,
    iterations: int = 500,
    l2: float = 0.01,
) -> StandardizedLogisticModel:
    if not rows or not feature_names:
        raise ValueError("training rows and feature_names are required")
    if learning_rate <= 0.0 or iterations <= 0 or l2 < 0.0:
        raise ValueError("invalid logistic training parameters")
    ordered = sorted(rows, key=lambda row: row.epoch)
    missing = [row.epoch for row in ordered if row.epoch not in outcomes]
    if missing:
        raise ValueError(f"missing outcomes for training epochs: {missing[:5]}")
    labels = [int(outcomes[row.epoch]) for row in ordered]
    if any(label not in (0, 1) for label in labels):
        raise ValueError("outcomes must be binary 0/1")

    means, scales = _training_stats(ordered, feature_names)
    matrix = [
        _standardized_vector(row, feature_names, means, scales) for row in ordered
    ]
    weights = [0.0] * len(feature_names)
    base_rate = (sum(labels) + 0.5) / (len(labels) + 1.0)
    intercept = math.log(base_rate / (1.0 - base_rate))
    n = float(len(ordered))

    for _ in range(iterations):
        gradient_intercept = 0.0
        gradients = [0.0] * len(weights)
        for vector, label in zip(matrix, labels, strict=True):
            score = intercept + sum(
                weight * value for weight, value in zip(weights, vector, strict=True)
            )
            error = _sigmoid(score) - label
            gradient_intercept += error
            for index, value in enumerate(vector):
                gradients[index] += error * value
        intercept -= learning_rate * gradient_intercept / n
        for index in range(len(weights)):
            gradient = gradients[index] / n + l2 * weights[index]
            weights[index] -= learning_rate * gradient

    identity = {
        "feature_names": feature_names,
        "train_max_epoch": ordered[-1].epoch,
        "learning_rate": learning_rate,
        "iterations": iterations,
        "l2": l2,
    }
    model_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    return StandardizedLogisticModel(
        feature_names=feature_names,
        means=means,
        scales=scales,
        weights=tuple(weights),
        intercept=intercept,
        train_max_epoch=ordered[-1].epoch,
        model_id=f"logistic-{model_hash}",
    )


def _settled_outcomes(replay: ReplaySnapshot) -> dict[int, int]:
    return {
        record.epoch: 1 if record.label == "bull" else 0
        for record in replay.rounds
        if record.label in ("bull", "bear")
    }


def _fit_fold_model_and_calibrator(
    training_rows: Sequence[ResearchFeatureRow],
    outcomes: Mapping[int, int],
    *,
    feature_names: tuple[str, ...],
    calibration_rounds: int,
    calibration_bins: int,
    calibration_shrinkage: int,
) -> tuple[StandardizedLogisticModel, HistogramCalibrator] | None:
    if calibration_rounds < 2:
        raise ValueError("calibration_rounds must be at least 2")
    if len(training_rows) <= calibration_rounds + 1:
        return None
    fit_rows = training_rows[:-calibration_rounds]
    calibration_rows = training_rows[-calibration_rounds:]
    model = fit_logistic_baseline(
        fit_rows, outcomes, feature_names=feature_names
    )
    calibration_points = [
        CalibrationPoint(model.predict_ppm(row), outcomes[row.epoch])
        for row in calibration_rows
    ]
    calibrator = fit_histogram_calibrator(
        calibration_points,
        bins=calibration_bins,
        shrinkage=calibration_shrinkage,
        train_max_epoch=calibration_rows[-1].epoch,
        model_id=f"{model.model_id}-cal",
    )
    return model, calibrator


def run_walkforward_baseline(
    replay: ReplaySnapshot,
    rows: Iterable[ResearchFeatureRow],
    *,
    feature_names: tuple[str, ...] = ALL_FEATURE_NAMES,
    feature_set_id: str = "full-v1",
    min_train_rounds: int = 200,
    test_rounds: int = 100,
    purge_rounds: int = 2,
    embargo_rounds: int = 2,
    calibration_rounds: int = 50,
    calibration_bins: int = 10,
    calibration_shrinkage: int = 20,
) -> WalkForwardBaselineResult:
    outcomes = _settled_outcomes(replay)
    by_epoch: dict[int, ResearchFeatureRow] = {}
    for row in rows:
        if row.market != replay.market or row.epoch not in outcomes:
            continue
        if row.epoch in by_epoch:
            raise ValueError(f"duplicate research feature row for epoch {row.epoch}")
        by_epoch[row.epoch] = row
    epochs = sorted(by_epoch)
    folds = generate_expanding_folds(
        epochs,
        min_train_rounds=min_train_rounds,
        test_rounds=test_rounds,
        purge_rounds=purge_rounds,
        embargo_rounds=embargo_rounds,
    )
    signals: dict[int, OosSignal] = {}
    calibration_failures = 0
    for fold in folds:
        training_rows = [
            by_epoch[epoch]
            for epoch in epochs
            if fold.train_start_epoch <= epoch <= fold.train_end_epoch
        ]
        fitted = _fit_fold_model_and_calibrator(
            training_rows,
            outcomes,
            feature_names=feature_names,
            calibration_rounds=calibration_rounds,
            calibration_bins=calibration_bins,
            calibration_shrinkage=calibration_shrinkage,
        )
        if fitted is None:
            calibration_failures += 1
            continue
        model, calibrator = fitted
        test_epochs = [
            epoch
            for epoch in epochs
            if fold.test_start_epoch <= epoch <= fold.test_end_epoch
        ]
        for epoch in test_epochs:
            row = by_epoch[epoch]
            raw_probability = model.predict_ppm(row)
            calibrated = calibrator.predict_ppm(raw_probability)
            signals[epoch] = OosSignal(
                epoch=epoch,
                p_bull_ppm=calibrated,
                generated_at=row.decision_timestamp_ms // 1_000,
                train_max_epoch=fold.train_end_epoch,
                fold=f"wf-{fold.fold}",
            )
    metrics = evaluate_oos(replay, signals, purge_rounds=purge_rounds)
    return WalkForwardBaselineResult(
        feature_set_id=feature_set_id,
        feature_names=feature_names,
        signals=signals,
        metrics=metrics,
        fold_count=len(folds),
        calibration_failures=calibration_failures,
    )


def run_feature_family_ablation(
    replay: ReplaySnapshot,
    rows: Iterable[ResearchFeatureRow],
    *,
    min_train_rounds: int = 200,
    test_rounds: int = 100,
    purge_rounds: int = 2,
    embargo_rounds: int = 2,
    calibration_rounds: int = 50,
) -> tuple[AblationResult, ...]:
    cached_rows = tuple(rows)
    results: list[AblationResult] = []
    variants: list[tuple[str | None, str]] = [(None, "full-v1")]
    variants.extend(
        (family, f"without-{family}-v1") for family in FEATURE_FAMILIES
    )
    for removed_family, feature_set_id in variants:
        feature_names = feature_names_without_family(removed_family)
        report = run_walkforward_baseline(
            replay,
            cached_rows,
            feature_names=feature_names,
            feature_set_id=feature_set_id,
            min_train_rounds=min_train_rounds,
            test_rounds=test_rounds,
            purge_rounds=purge_rounds,
            embargo_rounds=embargo_rounds,
            calibration_rounds=calibration_rounds,
        )
        metrics = report.metrics
        results.append(
            AblationResult(
                feature_set_id=feature_set_id,
                removed_family=removed_family,
                n_features=len(feature_names),
                n_scored=metrics.n_scored,
                brier_score=metrics.brier_score,
                brier_skill_score=metrics.brier_skill_score,
                log_loss=metrics.log_loss,
                ece_10=metrics.ece_10,
                accuracy=metrics.accuracy,
            )
        )
    return tuple(results)
