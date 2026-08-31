from __future__ import annotations

from pancake_prediction.baseline import ResearchFeatureRow, WalkForwardBaselineResult
from pancake_prediction.legacy_microstructure_model import (
    LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES,
    run_legacy_microstructure_v2_model,
)
from pancake_prediction.legacy_rounds import LegacyRoundRecord
from pancake_prediction.walkforward import validate_oos_provenance


def _round(epoch: int) -> LegacyRoundRecord:
    bullish = epoch % 2 == 1
    start = 1_000 + epoch * 300
    lock = start + 300
    close = lock + 300
    lock_price = 30_000_000_000
    return LegacyRoundRecord(
        epoch=epoch,
        start_timestamp=start,
        lock_timestamp=lock,
        close_timestamp=close,
        lock_price_e8=lock_price,
        close_price_e8=lock_price + (100_000_000 if bullish else -100_000_000),
        lock_oracle_id=10_000 + epoch,
        close_oracle_id=20_000 + epoch,
        total_amount_wei=2 * 10**18,
        bull_amount_wei=10**18,
        bear_amount_wei=10**18,
        reward_base_cal_amount_wei=10**18,
        reward_amount_wei=194 * 10**16,
        oracle_called=True,
    )


def _row(record: LegacyRoundRecord) -> ResearchFeatureRow:
    bullish = record.label == "bull"
    sign = 1.0 if bullish else -1.0
    values: dict[str, float | None] = {
        "spot_perp_basis_ppm": 100.0 * sign,
        "spot_flow_imbalance_ppm": 100_000.0 * sign,
        "perp_flow_imbalance_ppm": 80_000.0 * sign,
        "prior_bull_rate_20_ppm": 520_000.0 if bullish else 480_000.0,
        "prior_abs_return_12_ppm": 3_000.0 + record.epoch,
    }
    for prefix in ("spot", "perp"):
        for seconds in (5, 20, 60):
            values[f"{prefix}_return_{seconds}s_ppm"] = (300.0 + seconds) * sign
            values[f"{prefix}_flow_imbalance_{seconds}s_ppm"] = (
                150_000.0 + seconds * 100.0
            ) * sign
            values[f"{prefix}_trade_count_{seconds}s"] = float(seconds + record.epoch)
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=record.epoch,
        decision_timestamp_ms=(record.lock_timestamp - 20) * 1_000,
        values=values,
    )


def _fixture() -> tuple[tuple[LegacyRoundRecord, ...], tuple[ResearchFeatureRow, ...]]:
    rounds = tuple(_round(epoch) for epoch in range(1, 41))
    return rounds, tuple(_row(record) for record in rounds)


def _run(
    rounds: tuple[LegacyRoundRecord, ...],
    rows: tuple[ResearchFeatureRow, ...],
) -> WalkForwardBaselineResult:
    return run_legacy_microstructure_v2_model(
        rounds,
        rows,
        min_train_rounds=12,
        test_rounds=6,
        purge_rounds=2,
        embargo_rounds=1,
        calibration_rounds=4,
        calibration_bins=4,
        calibration_shrinkage=4,
    )


def test_microstructure_v2_uses_frozen_explicit_feature_set() -> None:
    rounds, rows = _fixture()
    report = _run(rounds, rows)

    assert report.feature_set_id == "legacy-cex-microstructure-v2"
    assert report.feature_names == LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES
    assert len(report.feature_names) == 23
    assert report.metrics.n_scored > 0
    validate_oos_provenance(report.signals.values(), purge_rounds=2)


def test_microstructure_v2_rejects_duplicate_epoch_rows() -> None:
    rounds, rows = _fixture()
    duplicate = (*rows, rows[0])
    try:
        _run(rounds, duplicate)
    except ValueError as exc:
        assert "duplicate legacy microstructure row" in str(exc)
    else:
        raise AssertionError("duplicate microstructure epoch must fail closed")


def test_microstructure_v2_ignores_unlisted_values() -> None:
    rounds, rows = _fixture()
    baseline = _run(rounds, rows)
    mutated = tuple(
        ResearchFeatureRow(
            market=row.market,
            epoch=row.epoch,
            decision_timestamp_ms=row.decision_timestamp_ms,
            values={
                **row.values,
                "future_target_price": float(row.epoch * 999_999_999),
                "final_bull_pool": float(row.epoch),
            },
        )
        for row in rows
    )
    rerun = _run(rounds, mutated)

    assert baseline.signals == rerun.signals
    assert baseline.metrics == rerun.metrics
