from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .backtest import (
    BacktestConfig,
    BacktestReport,
    BacktestSignal,
    PoolProjection,
    run_backtest,
)
from .replay import ChainEvent, ReplaySnapshot
from .walkforward import OosMetrics, OosSignal, evaluate_oos, validate_oos_provenance


@dataclass(frozen=True, slots=True)
class EconomicOosReport:
    feature_set_id: str
    probability_metrics: OosMetrics
    backtest: BacktestReport
    direction_signal_count: int
    pool_projection_count: int
    joint_epoch_count: int


def validate_projection_provenance(
    projections: Mapping[int, PoolProjection], *, purge_rounds: int = 2
) -> None:
    if purge_rounds < 0:
        raise ValueError("purge_rounds must be non-negative")
    for epoch, projection in projections.items():
        projection.validate()
        if epoch != projection.epoch:
            raise ValueError("pool projection mapping key does not match projection epoch")
        if projection.train_max_epoch is None:
            raise ValueError("OOS pool projection must include train_max_epoch provenance")
        latest_allowed = projection.epoch - purge_rounds - 1
        if projection.train_max_epoch > latest_allowed:
            raise ValueError(
                "pool projection is not purged OOS: "
                f"epoch={projection.epoch}, train_max_epoch={projection.train_max_epoch}, "
                f"latest_allowed={latest_allowed}"
            )


def run_oos_economic_backtest(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    direction_signals: Mapping[int, OosSignal],
    pool_projections: Mapping[int, PoolProjection],
    config: BacktestConfig,
    *,
    feature_set_id: str,
    purge_rounds: int = 2,
) -> EconomicOosReport:
    validate_oos_provenance(direction_signals.values(), purge_rounds=purge_rounds)
    validate_projection_provenance(pool_projections, purge_rounds=purge_rounds)
    probability_metrics = evaluate_oos(
        replay, dict(direction_signals), purge_rounds=purge_rounds
    )
    signals = {
        epoch: BacktestSignal(
            epoch=signal.epoch,
            p_bull_ppm=signal.p_bull_ppm,
            generated_at=signal.generated_at,
            model_id=f"{feature_set_id}:{signal.fold or 'oos'}",
            train_max_epoch=signal.train_max_epoch,
        )
        for epoch, signal in direction_signals.items()
    }
    projections = dict(pool_projections)
    backtest = run_backtest(replay, events, signals, projections, config)
    joint_epochs = set(direction_signals).intersection(pool_projections)
    return EconomicOosReport(
        feature_set_id=feature_set_id,
        probability_metrics=probability_metrics,
        backtest=backtest,
        direction_signal_count=len(direction_signals),
        pool_projection_count=len(pool_projections),
        joint_epoch_count=len(joint_epochs),
    )
