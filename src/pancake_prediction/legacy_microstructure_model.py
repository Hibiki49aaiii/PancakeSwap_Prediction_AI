from __future__ import annotations

from collections.abc import Iterable

from .baseline import ResearchFeatureRow, WalkForwardBaselineResult, fit_logistic_baseline
from .calibration import CalibrationPoint, fit_histogram_calibrator
from .legacy_model import LEGACY_FEATURE_NAMES
from .legacy_rounds import LegacyRoundRecord
from .walkforward import OosSignal, evaluate_binary_oos, generate_expanding_folds

MICROSTRUCTURE_FEATURE_NAMES = (
    "spot_return_5s_ppm",
    "spot_return_20s_ppm",
    "spot_return_60s_ppm",
    "spot_flow_imbalance_5s_ppm",
    "spot_flow_imbalance_20s_ppm",
    "spot_flow_imbalance_60s_ppm",
    "spot_trade_count_5s",
    "spot_trade_count_20s",
    "spot_trade_count_60s",
    "perp_return_5s_ppm",
    "perp_return_20s_ppm",
    "perp_return_60s_ppm",
    "perp_flow_imbalance_5s_ppm",
    "perp_flow_imbalance_20s_ppm",
    "perp_flow_imbalance_60s_ppm",
    "perp_trade_count_5s",
    "perp_trade_count_20s",
    "perp_trade_count_60s",
)

LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES = LEGACY_FEATURE_NAMES + MICROSTRUCTURE_FEATURE_NAMES


def _outcomes(
    rounds: tuple[LegacyRoundRecord, ...],
) -> tuple[dict[int, int], dict[int, int], int]:
    outcomes: dict[int, int] = {}
    generated_at_floors: dict[int, int] = {}
    ties = 0
    for record in rounds:
        if not record.oracle_called:
            continue
        if record.label == "tie":
            ties += 1
            continue
        outcomes[record.epoch] = 1 if record.label == "bull" else 0
        generated_at_floors[record.epoch] = record.start_timestamp
    return outcomes, generated_at_floors, ties


def run_legacy_microstructure_model(
    rounds: tuple[LegacyRoundRecord, ...],
    rows: Iterable[ResearchFeatureRow],
    *,
    feature_names: tuple[str, ...],
    feature_set_id: str,
    fold_prefix: str,
    min_train_rounds: int = 200,
    test_rounds: int = 100,
    purge_rounds: int = 2,
    embargo_rounds: int = 2,
    calibration_rounds: int = 50,
    calibration_bins: int = 10,
    calibration_shrinkage: int = 20,
) -> WalkForwardBaselineResult:
    """Evaluate an explicit microstructure feature set under one OOS discipline."""

    if not feature_names or len(set(feature_names)) != len(feature_names):
        raise ValueError("feature_names must be non-empty and unique")
    if not feature_set_id.strip() or not fold_prefix.strip():
        raise ValueError("feature_set_id and fold_prefix are required")
    if calibration_rounds < 2:
        raise ValueError("calibration_rounds must be at least 2")
    outcomes, generated_at_floors, ties = _outcomes(rounds)
    by_epoch: dict[int, ResearchFeatureRow] = {}
    for row in rows:
        if row.market != "BNBUSD" or row.epoch not in outcomes:
            continue
        if row.epoch in by_epoch:
            raise ValueError(f"duplicate legacy microstructure row for epoch {row.epoch}")
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
        if len(training_rows) <= calibration_rounds + 1:
            calibration_failures += 1
            continue
        fit_rows = training_rows[:-calibration_rounds]
        calibration_rows = training_rows[-calibration_rounds:]
        model = fit_logistic_baseline(
            fit_rows,
            outcomes,
            feature_names=feature_names,
        )
        calibrator = fit_histogram_calibrator(
            [
                CalibrationPoint(model.predict_ppm(row), outcomes[row.epoch])
                for row in calibration_rows
            ],
            bins=calibration_bins,
            shrinkage=calibration_shrinkage,
            train_max_epoch=calibration_rows[-1].epoch,
            model_id=f"{model.model_id}-{feature_set_id}-cal",
        )
        for epoch in epochs:
            if not fold.test_start_epoch <= epoch <= fold.test_end_epoch:
                continue
            row = by_epoch[epoch]
            signals[epoch] = OosSignal(
                epoch=epoch,
                p_bull_ppm=calibrator.predict_ppm(model.predict_ppm(row)),
                generated_at=row.decision_timestamp_ms // 1_000,
                train_max_epoch=fold.train_end_epoch,
                fold=f"{fold_prefix}-{fold.fold}",
            )

    metrics = evaluate_binary_oos(
        market="BNBUSD",
        outcomes=outcomes,
        signals=signals,
        purge_rounds=purge_rounds,
        generated_at_floor=generated_at_floors,
        n_ties_excluded=ties,
    )
    return WalkForwardBaselineResult(
        feature_set_id=feature_set_id,
        feature_names=feature_names,
        signals=signals,
        metrics=metrics,
        fold_count=len(folds),
        calibration_failures=calibration_failures,
    )


def run_legacy_microstructure_v2_model(
    rounds: tuple[LegacyRoundRecord, ...],
    rows: Iterable[ResearchFeatureRow],
    *,
    min_train_rounds: int = 200,
    test_rounds: int = 100,
    purge_rounds: int = 2,
    embargo_rounds: int = 2,
    calibration_rounds: int = 50,
    calibration_bins: int = 10,
    calibration_shrinkage: int = 20,
) -> WalkForwardBaselineResult:
    """Evaluate the frozen 23-feature v2 under purged/embargoed OOS folds."""

    return run_legacy_microstructure_model(
        rounds,
        rows,
        feature_names=LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES,
        feature_set_id="legacy-cex-microstructure-v2",
        fold_prefix="legacy-micro-v2-wf",
        min_train_rounds=min_train_rounds,
        test_rounds=test_rounds,
        purge_rounds=purge_rounds,
        embargo_rounds=embargo_rounds,
        calibration_rounds=calibration_rounds,
        calibration_bins=calibration_bins,
        calibration_shrinkage=calibration_shrinkage,
    )
