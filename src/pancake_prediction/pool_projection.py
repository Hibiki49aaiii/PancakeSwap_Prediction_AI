from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from statistics import median

from .backtest import (
    BacktestConfig,
    PoolProjection,
    build_decision_snapshot,
    build_event_index,
)
from .replay import ChainEvent, ReplaySnapshot, RoundRecord

RATIO_SCALE = 1_000_000


@dataclass(frozen=True, slots=True)
class PoolProjectionBaselineConfig:
    min_train_rounds: int = 50
    window_rounds: int = 500
    purge_rounds: int = 2

    def validate(self) -> None:
        if self.min_train_rounds <= 0:
            raise ValueError("min_train_rounds must be positive")
        if self.window_rounds < self.min_train_rounds:
            raise ValueError("window_rounds must be >= min_train_rounds")
        if self.purge_rounds < 0:
            raise ValueError("purge_rounds must be non-negative")


@dataclass(frozen=True, slots=True)
class _PoolGrowthTarget:
    epoch: int
    bull_growth_ratio_ppm: int
    bear_growth_ratio_ppm: int


def _growth_target(
    replay: ReplaySnapshot,
    record: RoundRecord,
    events: tuple[ChainEvent, ...],
    backtest_config: BacktestConfig,
) -> _PoolGrowthTarget | None:
    snapshot = build_decision_snapshot(replay, record, events, backtest_config)
    if snapshot is None or snapshot.total_observed_wei <= 0:
        return None
    if record.bull_amount_wei < snapshot.bull_observed_wei:
        return None
    if record.bear_amount_wei < snapshot.bear_observed_wei:
        return None
    bull_growth = record.bull_amount_wei - snapshot.bull_observed_wei
    bear_growth = record.bear_amount_wei - snapshot.bear_observed_wei
    return _PoolGrowthTarget(
        epoch=record.epoch,
        bull_growth_ratio_ppm=bull_growth * RATIO_SCALE // snapshot.total_observed_wei,
        bear_growth_ratio_ppm=bear_growth * RATIO_SCALE // snapshot.total_observed_wei,
    )


def _model_id(config: PoolProjectionBaselineConfig, training: tuple[_PoolGrowthTarget, ...]) -> str:
    payload = {
        "config": asdict(config),
        "training": [asdict(row) for row in training],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    return f"pool-growth-median-{digest}"


def build_oos_pool_projections(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    backtest_config: BacktestConfig,
    *,
    config: PoolProjectionBaselineConfig | None = None,
) -> dict[int, PoolProjection]:
    resolved_config = PoolProjectionBaselineConfig() if config is None else config
    resolved_config.validate()
    backtest_config.validate()
    event_index = build_event_index(events)
    ordered = sorted(replay.rounds, key=lambda record: record.epoch)
    result: dict[int, PoolProjection] = {}

    for target in ordered:
        target_snapshot = build_decision_snapshot(
            replay,
            target,
            events,
            backtest_config,
            event_index=event_index,
        )
        if target_snapshot is None or target_snapshot.total_observed_wei <= 0:
            continue
        latest_allowed_epoch = target.epoch - resolved_config.purge_rounds - 1
        candidates: list[_PoolGrowthTarget] = []
        for prior in ordered:
            if prior.epoch > latest_allowed_epoch:
                break
            if (
                prior.end_timestamp is None
                or prior.end_timestamp >= target_snapshot.decision_timestamp
            ):
                continue
            growth = _growth_target(replay, prior, events, backtest_config)
            if growth is not None:
                candidates.append(growth)
        if len(candidates) < resolved_config.min_train_rounds:
            continue
        training = tuple(candidates[-resolved_config.window_rounds :])
        bull_ratio = int(median(row.bull_growth_ratio_ppm for row in training))
        bear_ratio = int(median(row.bear_growth_ratio_ppm for row in training))
        observed_total = target_snapshot.total_observed_wei
        projected_bull = (
            target_snapshot.bull_observed_wei
            + observed_total * max(0, bull_ratio) // RATIO_SCALE
        )
        projected_bear = (
            target_snapshot.bear_observed_wei
            + observed_total * max(0, bear_ratio) // RATIO_SCALE
        )
        result[target.epoch] = PoolProjection(
            epoch=target.epoch,
            generated_at=target_snapshot.decision_timestamp,
            projected_bull_wei=projected_bull,
            projected_bear_wei=projected_bear,
            model_id=_model_id(resolved_config, training),
            train_max_epoch=training[-1].epoch,
        )
    return result
