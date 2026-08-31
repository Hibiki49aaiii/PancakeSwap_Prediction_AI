from __future__ import annotations

from collections.abc import Iterable

from .baseline import ResearchFeatureRow, WalkForwardBaselineResult, fit_logistic_baseline
from .calibration import CalibrationPoint, fit_histogram_calibrator
from .replay import ReplaySnapshot
from .walkforward import OosSignal, evaluate_oos, generate_expanding_folds

RECENT_CEX_FEATURE_NAMES = (
    "spot_perp_basis_ppm",
    "spot_flow_imbalance_ppm",
    "perp_flow_imbalance_ppm",
    "prior_bull_rate_20_ppm",
    "prior_abs_return_12_ppm",
)


def run_recent_canonical_cex_model(
    replay: ReplaySnapshot,
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
    if calibration_rounds < 2:
        raise ValueError("calibration_rounds must be at least 2")
    outcomes = {
        record.epoch: 1 if record.label == "bull" else 0
        for record in replay.rounds
        if record.label in {"bull", "bear"}
    }
    by_epoch: dict[int, ResearchFeatureRow] = {}
    for row in rows:
        if row.market != replay.market or row.epoch not in outcomes:
            continue
        if row.epoch in by_epoch:
            raise ValueError(f"duplicate recent feature row for epoch {row.epoch}")
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
            feature_names=RECENT_CEX_FEATURE_NAMES,
        )
        calibrator = fit_histogram_calibrator(
            [
                CalibrationPoint(model.predict_ppm(row), outcomes[row.epoch])
                for row in calibration_rows
            ],
            bins=calibration_bins,
            shrinkage=calibration_shrinkage,
            train_max_epoch=calibration_rows[-1].epoch,
            model_id=f"{model.model_id}-recent-cex-cal",
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
                fold=f"recent-cex-wf-{fold.fold}",
            )

    metrics = evaluate_oos(replay, signals, purge_rounds=purge_rounds)
    return WalkForwardBaselineResult(
        feature_set_id="recent-canonical-cex-history-v1",
        feature_names=RECENT_CEX_FEATURE_NAMES,
        signals=signals,
        metrics=metrics,
        fold_count=len(folds),
        calibration_failures=calibration_failures,
    )
