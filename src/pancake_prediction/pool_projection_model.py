from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from statistics import median

from .backtest import PoolProjection
from .pool_projection_dataset import PoolProjectionDataset, PoolProjectionDatasetRow

RATIO_SCALE = 1_000_000


@dataclass(frozen=True, slots=True)
class PoolProjectionKnnConfig:
    min_train_rounds: int = 100
    window_rounds: int = 1_000
    purge_rounds: int = 2
    neighbors: int = 25

    def validate(self) -> None:
        if self.min_train_rounds <= 0:
            raise ValueError("min_train_rounds must be positive")
        if self.window_rounds < self.min_train_rounds:
            raise ValueError("window_rounds must be >= min_train_rounds")
        if self.purge_rounds < 0:
            raise ValueError("purge_rounds must be non-negative")
        if self.neighbors <= 0 or self.neighbors > self.window_rounds:
            raise ValueError("neighbors must be in [1, window_rounds]")


@dataclass(frozen=True, slots=True)
class PoolProjectionModelReport:
    projections: dict[int, PoolProjection]
    feature_names: tuple[str, ...]
    skipped_no_observed_pool: int
    skipped_insufficient_history: int


_FEATURE_NAMES = (
    "observed_bull_share_ppm",
    "flow_imbalance_5s_ppm",
    "flow_imbalance_20s_ppm",
    "flow_imbalance_60s_ppm",
    "flow_total_5s_ppm",
    "flow_total_20s_ppm",
    "flow_total_60s_ppm",
    "bet_count",
    "unique_bettors",
)


def _growth_ratios(row: PoolProjectionDatasetRow) -> tuple[int, int] | None:
    features = row.features
    observed_total = features.observed_bull_wei + features.observed_bear_wei
    if observed_total <= 0:
        return None
    bull_growth = row.label.final_bull_wei - features.observed_bull_wei
    bear_growth = row.label.final_bear_wei - features.observed_bear_wei
    if bull_growth < 0 or bear_growth < 0:
        raise ValueError("pool projection label shrank below observed pool")
    return (
        bull_growth * RATIO_SCALE // observed_total,
        bear_growth * RATIO_SCALE // observed_total,
    )


def _flow_total_ppm(bull_wei: int, bear_wei: int, observed_total: int) -> float | None:
    if observed_total <= 0:
        return None
    return (bull_wei + bear_wei) * RATIO_SCALE / observed_total


def _vector(row: PoolProjectionDatasetRow) -> tuple[float | None, ...]:
    features = row.features
    observed_total = features.observed_bull_wei + features.observed_bear_wei
    return (
        None if features.observed_bull_share_ppm is None else float(features.observed_bull_share_ppm),
        None if features.flow_imbalance_5s_ppm is None else float(features.flow_imbalance_5s_ppm),
        None if features.flow_imbalance_20s_ppm is None else float(features.flow_imbalance_20s_ppm),
        None if features.flow_imbalance_60s_ppm is None else float(features.flow_imbalance_60s_ppm),
        _flow_total_ppm(features.bull_flow_5s_wei, features.bear_flow_5s_wei, observed_total),
        _flow_total_ppm(features.bull_flow_20s_wei, features.bear_flow_20s_wei, observed_total),
        _flow_total_ppm(features.bull_flow_60s_wei, features.bear_flow_60s_wei, observed_total),
        float(features.bet_count),
        float(features.unique_bettors),
    )


def _training_stats(vectors: tuple[tuple[float | None, ...], ...]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    means: list[float] = []
    scales: list[float] = []
    for column in range(len(_FEATURE_NAMES)):
        observed = [vector[column] for vector in vectors if vector[column] is not None]
        values = [float(value) for value in observed]
        if not values:
            means.append(0.0)
            scales.append(1.0)
            continue
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean)
        scales.append(max(math.sqrt(variance), 1e-9))
    return tuple(means), tuple(scales)


def _standardize(
    vector: tuple[float | None, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> tuple[float, ...]:
    return tuple(
        0.0 if value is None else (float(value) - mean) / scale
        for value, mean, scale in zip(vector, means, scales, strict=True)
    )


def _distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True))


def _model_id(
    config: PoolProjectionKnnConfig,
    training: tuple[PoolProjectionDatasetRow, ...],
) -> str:
    payload = {
        "config": asdict(config),
        "features": _FEATURE_NAMES,
        "training_epochs": [row.features.epoch for row in training],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256((raw + "\n").encode()).hexdigest()[:12]
    return f"pool-knn-{digest}"


def build_oos_feature_conditioned_pool_projections(
    dataset: PoolProjectionDataset,
    *,
    config: PoolProjectionKnnConfig | None = None,
) -> PoolProjectionModelReport:
    resolved = PoolProjectionKnnConfig() if config is None else config
    resolved.validate()
    ordered = tuple(sorted(dataset.rows, key=lambda row: row.features.epoch))
    projections: dict[int, PoolProjection] = {}
    skipped_no_pool = 0
    skipped_history = 0

    for target in ordered:
        target_features = target.features
        observed_total = target_features.observed_bull_wei + target_features.observed_bear_wei
        if observed_total <= 0:
            skipped_no_pool += 1
            continue
        latest_allowed_epoch = target_features.epoch - resolved.purge_rounds - 1
        candidates = tuple(
            row
            for row in ordered
            if row.features.epoch <= latest_allowed_epoch
            and row.features.scheduled_lock_timestamp < target_features.decision_timestamp
            and _growth_ratios(row) is not None
        )
        if len(candidates) < resolved.min_train_rounds:
            skipped_history += 1
            continue
        training = candidates[-resolved.window_rounds :]
        vectors = tuple(_vector(row) for row in training)
        means, scales = _training_stats(vectors)
        target_vector = _standardize(_vector(target), means, scales)
        ranked = sorted(
            zip(training, vectors, strict=True),
            key=lambda item: _distance(
                _standardize(item[1], means, scales),
                target_vector,
            ),
        )
        neighbors = tuple(row for row, _ in ranked[: min(resolved.neighbors, len(ranked))])
        ratios = tuple(_growth_ratios(row) for row in neighbors)
        bull_ratios = [ratio[0] for ratio in ratios if ratio is not None]
        bear_ratios = [ratio[1] for ratio in ratios if ratio is not None]
        if not bull_ratios or not bear_ratios:
            skipped_history += 1
            continue
        bull_ratio = int(median(bull_ratios))
        bear_ratio = int(median(bear_ratios))
        projections[target_features.epoch] = PoolProjection(
            epoch=target_features.epoch,
            generated_at=target_features.decision_timestamp,
            projected_bull_wei=(
                target_features.observed_bull_wei
                + observed_total * max(0, bull_ratio) // RATIO_SCALE
            ),
            projected_bear_wei=(
                target_features.observed_bear_wei
                + observed_total * max(0, bear_ratio) // RATIO_SCALE
            ),
            model_id=_model_id(resolved, training),
            train_max_epoch=training[-1].features.epoch,
        )

    return PoolProjectionModelReport(
        projections=projections,
        feature_names=_FEATURE_NAMES,
        skipped_no_observed_pool=skipped_no_pool,
        skipped_insufficient_history=skipped_history,
    )
