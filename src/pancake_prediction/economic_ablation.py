from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .backtest import BacktestConfig, PoolProjection
from .baseline import (
    FEATURE_FAMILIES,
    ResearchFeatureRow,
    feature_names_without_family,
    run_walkforward_baseline,
)
from .economic_oos import run_oos_economic_backtest, validate_projection_provenance
from .replay import ChainEvent, ReplaySnapshot
from .walkforward import OosSignal


@dataclass(frozen=True, slots=True)
class EconomicAblationResult:
    feature_set_id: str
    removed_family: str | None
    n_features: int
    common_epoch_count: int
    n_scored: int
    brier_score: float | None
    brier_skill_score: float | None
    log_loss: float | None
    ece_10: float | None
    accuracy: float | None
    trade_count: int
    pnl_wei: int
    roi_ppm: int | None
    max_drawdown_wei: int
    skipped_no_positive_ev: int
    skipped_late: int
    skipped_integrity: int


def _variants() -> tuple[tuple[str | None, str], ...]:
    values: list[tuple[str | None, str]] = [(None, "full-v1")]
    values.extend((family, f"without-{family}-v1") for family in FEATURE_FAMILIES)
    return tuple(values)


def run_economic_feature_ablation(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    rows: Iterable[ResearchFeatureRow],
    pool_projections: Mapping[int, PoolProjection],
    backtest_config: BacktestConfig,
    *,
    min_train_rounds: int = 200,
    test_rounds: int = 100,
    purge_rounds: int = 2,
    embargo_rounds: int = 2,
    calibration_rounds: int = 50,
) -> tuple[EconomicAblationResult, ...]:
    validate_projection_provenance(pool_projections, purge_rounds=purge_rounds)
    cached_rows = tuple(rows)
    model_reports: list[
        tuple[str | None, str, tuple[str, ...], Mapping[int, OosSignal]]
    ] = []
    for removed_family, feature_set_id in _variants():
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
        model_reports.append(
            (removed_family, feature_set_id, feature_names, report.signals)
        )

    common_epochs = set(pool_projections)
    for _removed, _feature_set_id, _feature_names, signals in model_reports:
        common_epochs.intersection_update(signals)
    common = frozenset(common_epochs)
    common_projections = {
        epoch: projection
        for epoch, projection in pool_projections.items()
        if epoch in common
    }

    results: list[EconomicAblationResult] = []
    for removed_family, feature_set_id, feature_names, signals in model_reports:
        common_signals = {
            epoch: signal for epoch, signal in signals.items() if epoch in common
        }
        economic = run_oos_economic_backtest(
            replay,
            events,
            common_signals,
            common_projections,
            backtest_config,
            feature_set_id=feature_set_id,
            purge_rounds=purge_rounds,
        )
        probability = economic.probability_metrics
        backtest = economic.backtest
        results.append(
            EconomicAblationResult(
                feature_set_id=feature_set_id,
                removed_family=removed_family,
                n_features=len(feature_names),
                common_epoch_count=len(common),
                n_scored=probability.n_scored,
                brier_score=probability.brier_score,
                brier_skill_score=probability.brier_skill_score,
                log_loss=probability.log_loss,
                ece_10=probability.ece_10,
                accuracy=probability.accuracy,
                trade_count=len(backtest.trades),
                pnl_wei=backtest.pnl_wei,
                roi_ppm=backtest.roi_ppm,
                max_drawdown_wei=backtest.max_drawdown_wei,
                skipped_no_positive_ev=backtest.skipped_no_positive_ev,
                skipped_late=backtest.skipped_late,
                skipped_integrity=backtest.skipped_integrity,
            )
        )
    return tuple(results)
