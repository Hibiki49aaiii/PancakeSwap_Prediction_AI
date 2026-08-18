from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from statistics import median

from .backtest import BacktestConfig, PoolProjection, build_decision_snapshot, build_event_index
from .replay import ChainEvent, ReplaySnapshot, RoundRecord


@dataclass(frozen=True, slots=True)
class AbsolutePoolProjectionConfig:
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
class _SettledPoolTarget:
    epoch: int
    bull_amount_wei: int
    bear_amount_wei: int


def _eligible_prior_pool(record: RoundRecord) -> _SettledPoolTarget | None:
    if record.label not in {"bull", "bear", "tie"}:
        return None
    if record.end_timestamp is None:
        return None
    if record.bull_amount_wei < 0 or record.bear_amount_wei < 0:
        return None
    if record.total_amount_wei <= 0:
        return None
    return _SettledPoolTarget(
        epoch=record.epoch,
        bull_amount_wei=record.bull_amount_wei,
        bear_amount_wei=record.bear_amount_wei,
    )


def _model_id(
    config: AbsolutePoolProjectionConfig,
    training: tuple[_SettledPoolTarget, ...],
) -> str:
    payload = {
        "config": asdict(config),
        "training": [asdict(row) for row in training],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    return f"prior-final-absolute-median-{digest}"


def build_prior_settled_absolute_pool_projections(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    backtest_config: BacktestConfig,
    *,
    config: AbsolutePoolProjectionConfig | None = None,
) -> dict[int, PoolProjection]:
    """Forecast target final pools using only older, already-settled rounds.

    The target round's observed or final Bull/Bear pool is never consulted while
    constructing its forecast. This makes the baseline suitable as a supporting
    robustness benchmark when target-round bet-flow history is unavailable.
    """

    resolved = AbsolutePoolProjectionConfig() if config is None else config
    resolved.validate()
    backtest_config.validate()
    event_index = build_event_index(events)
    ordered = tuple(sorted(replay.rounds, key=lambda record: record.epoch))
    result: dict[int, PoolProjection] = {}

    for target in ordered:
        snapshot = build_decision_snapshot(
            replay,
            target,
            events,
            backtest_config,
            event_index=event_index,
        )
        if snapshot is None:
            continue
        latest_allowed_epoch = target.epoch - resolved.purge_rounds - 1
        candidates: list[_SettledPoolTarget] = []
        for prior in ordered:
            if prior.epoch > latest_allowed_epoch:
                break
            if prior.end_timestamp is None or prior.end_timestamp >= snapshot.decision_timestamp:
                continue
            pool = _eligible_prior_pool(prior)
            if pool is not None:
                candidates.append(pool)
        if len(candidates) < resolved.min_train_rounds:
            continue
        training = tuple(candidates[-resolved.window_rounds :])
        projected_bull = int(median(row.bull_amount_wei for row in training))
        projected_bear = int(median(row.bear_amount_wei for row in training))
        result[target.epoch] = PoolProjection(
            epoch=target.epoch,
            generated_at=snapshot.decision_timestamp,
            projected_bull_wei=max(0, projected_bull),
            projected_bear_wei=max(0, projected_bear),
            model_id=_model_id(resolved, training),
            train_max_epoch=training[-1].epoch,
        )
    return result
