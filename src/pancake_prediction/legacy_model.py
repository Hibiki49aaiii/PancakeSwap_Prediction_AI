from __future__ import annotations

from collections.abc import Iterable, Mapping

from .backtest import BacktestSignal
from .baseline import (
    ResearchFeatureRow,
    WalkForwardBaselineResult,
    fit_logistic_baseline,
)
from .calibration import CalibrationPoint, fit_histogram_calibrator
from .legacy_rounds import LegacyRoundRecord
from .walkforward import OosSignal, evaluate_binary_oos, generate_expanding_folds

LEGACY_FEATURE_NAMES = (
    "spot_perp_basis_ppm",
    "spot_flow_imbalance_ppm",
    "perp_flow_imbalance_ppm",
    "prior_bull_rate_20_ppm",
    "prior_abs_return_12_ppm",
)


def _legacy_outcomes(
    rounds: tuple[LegacyRoundRecord, ...],
) -> tuple[dict[int, int], dict[int, int], int]:
    outcomes: dict[int, int] = {}
    floors: dict[int, int] = {}
    ties = 0
    for record in rounds:
        if not record.oracle_called:
            continue
        if record.label == "tie":
            ties += 1
            continue
        outcomes[record.epoch] = 1 if record.label == "bull" else 0
        floors[record.epoch] = record.start_timestamp
    return outcomes, floors, ties


def legacy_oos_to_backtest_signals(
    signals: Mapping[int, OosSignal],
) -> dict[int, BacktestSignal]:
    """Convert generic OOS signals into the economic backtest contract explicitly."""

    converted: dict[int, BacktestSignal] = {}
    for epoch, signal in signals.items():
        if epoch != signal.epoch:
            raise ValueError(f"legacy signal map key/epoch mismatch at epoch {epoch}")
        model_id = signal.fold or "legacy-wf-unlabeled"
        candidate = BacktestSignal(
            epoch=signal.epoch,
            p_bull_ppm=signal.p_bull_ppm,
            generated_at=signal.generated_at,
            model_id=model_id,
            train_max_epoch=signal.train_max_epoch,
        )
        candidate.validate()
        converted[epoch] = candidate
    return converted


def run_legacy_walkforward_model(
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
    """Run the canonical model discipline on non-authoritative legacy outcomes.

    The feature set intentionally excludes every Chainlink and target-pool feature
    because the legacy round source cannot establish those values before the
    target decision cutoff.
    """

    if calibration_rounds < 2:
        raise ValueError("calibration_rounds must be at least 2")
    outcomes, floors, ties = _legacy_outcomes(rounds)
    by_epoch: dict[int, ResearchFeatureRow] = {}
    for row in rows:
        if row.market != "BNBUSD" or row.epoch not in outcomes:
            continue
        if row.epoch in by_epoch:
            raise ValueError(f"duplicate legacy research feature row for epoch {row.epoch}")
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
            feature_names=LEGACY_FEATURE_NAMES,
        )
        calibrator = fit_histogram_calibrator(
            [
                CalibrationPoint(model.predict_ppm(row), outcomes[row.epoch])
                for row in calibration_rows
            ],
            bins=calibration_bins,
            shrinkage=calibration_shrinkage,
            train_max_epoch=calibration_rows[-1].epoch,
            model_id=f"{model.model_id}-legacy-cal",
        )
        for epoch in epochs:
            if not fold.test_start_epoch <= epoch <= fold.test_end_epoch:
                continue
            row = by_epoch[epoch]
            probability = calibrator.predict_ppm(model.predict_ppm(row))
            signals[epoch] = OosSignal(
                epoch=epoch,
                p_bull_ppm=probability,
                generated_at=row.decision_timestamp_ms // 1_000,
                train_max_epoch=fold.train_end_epoch,
                fold=f"legacy-wf-{fold.fold}",
            )

    metrics = evaluate_binary_oos(
        market="BNBUSD",
        outcomes=outcomes,
        signals=signals,
        purge_rounds=purge_rounds,
        generated_at_floor=floors,
        n_ties_excluded=ties,
    )
    return WalkForwardBaselineResult(
        feature_set_id="legacy-cex-history-v1",
        feature_names=LEGACY_FEATURE_NAMES,
        signals=signals,
        metrics=metrics,
        fold_count=len(folds),
        calibration_failures=calibration_failures,
    )
