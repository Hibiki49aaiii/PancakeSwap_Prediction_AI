from __future__ import annotations

from dataclasses import replace

from pancake_prediction.baseline import ResearchFeatureRow, WalkForwardBaselineResult
from pancake_prediction.legacy_model import (
    LEGACY_FEATURE_NAMES,
    run_legacy_walkforward_model,
)
from pancake_prediction.legacy_rounds import LegacyRoundRecord
from pancake_prediction.walkforward import validate_oos_provenance


def _round(
    epoch: int,
    *,
    label: str,
    oracle_called: bool = True,
) -> LegacyRoundRecord:
    start = 1_000 + epoch * 300
    lock = start + 300
    close = lock + 300
    lock_price = 30_000_000_000
    close_price = (
        lock_price + 100_000_000
        if label == "bull"
        else lock_price - 100_000_000
        if label == "bear"
        else lock_price
    )
    return LegacyRoundRecord(
        epoch=epoch,
        start_timestamp=start,
        lock_timestamp=lock,
        close_timestamp=close,
        lock_price_e8=lock_price,
        close_price_e8=close_price,
        lock_oracle_id=10_000 + epoch,
        close_oracle_id=20_000 + epoch,
        total_amount_wei=2 * 10**18,
        bull_amount_wei=10**18,
        bear_amount_wei=10**18,
        reward_base_cal_amount_wei=10**18,
        reward_amount_wei=194 * 10**16,
        oracle_called=oracle_called,
    )


def _row(record: LegacyRoundRecord) -> ResearchFeatureRow:
    bullish = record.label == "bull"
    sign = 1.0 if bullish else -1.0
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=record.epoch,
        decision_timestamp_ms=(record.lock_timestamp - 20) * 1_000,
        values={
            "spot_perp_basis_ppm": 100.0 * sign,
            "spot_flow_imbalance_ppm": 500_000.0 * sign,
            "perp_flow_imbalance_ppm": 250_000.0 * sign,
            "prior_bull_rate_20_ppm": 600_000.0 if bullish else 400_000.0,
            "prior_abs_return_12_ppm": 3_000.0 + record.epoch,
            "oracle_age_ms": 999_999_999.0,
            "spot_oracle_gap_ppm": 999_999_999.0,
            "pool_bull_share_ppm": 999_999_999.0,
        },
    )


def _fixture() -> tuple[tuple[LegacyRoundRecord, ...], tuple[ResearchFeatureRow, ...]]:
    rounds = tuple(
        _round(epoch, label="bull" if epoch % 2 else "bear")
        for epoch in range(1, 31)
    )
    return rounds, tuple(_row(record) for record in rounds)


def _run(
    rounds: tuple[LegacyRoundRecord, ...],
    rows: tuple[ResearchFeatureRow, ...],
) -> WalkForwardBaselineResult:
    return run_legacy_walkforward_model(
        rounds,
        rows,
        min_train_rounds=10,
        test_rounds=5,
        purge_rounds=2,
        embargo_rounds=1,
        calibration_rounds=4,
        calibration_bins=4,
        calibration_shrinkage=4,
    )


def test_legacy_model_uses_only_explicit_cex_history_feature_set() -> None:
    rounds, rows = _fixture()
    report = _run(rounds, rows)

    assert report.feature_names == LEGACY_FEATURE_NAMES
    assert report.feature_set_id == "legacy-cex-history-v1"
    assert report.fold_count > 0
    assert report.metrics.n_scored > 0
    validate_oos_provenance(report.signals.values(), purge_rounds=2)
    assert all(
        signal.fold and signal.fold.startswith("legacy-wf-")
        for signal in report.signals.values()
    )


def test_unavailable_oracle_and_pool_values_cannot_change_legacy_predictions() -> None:
    rounds, rows = _fixture()
    original = _run(rounds, rows)
    mutated_rows = tuple(
        replace(
            row,
            values={
                **row.values,
                "oracle_age_ms": -123_456_789.0,
                "spot_oracle_gap_ppm": -987_654_321.0,
                "pool_bull_share_ppm": float(row.epoch * 999_999),
                "pool_log_total_bnb": 1e12,
            },
        )
        for row in rows
    )
    mutated = _run(rounds, mutated_rows)

    assert original.signals == mutated.signals
    assert original.metrics == mutated.metrics


def test_legacy_model_excludes_refunds_and_ties_from_binary_scoring() -> None:
    rounds, rows = _fixture()
    changed = list(rounds)
    changed[20] = _round(21, label="tie")
    changed[21] = _round(22, label="bear", oracle_called=False)
    report = _run(tuple(changed), rows)

    assert report.metrics.n_ties_excluded == 1
    assert 21 not in report.signals
    assert 22 not in report.signals
