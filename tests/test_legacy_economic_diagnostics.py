from __future__ import annotations

from pancake_prediction.backtest import BacktestSignal, PoolProjection
from pancake_prediction.legacy_benchmark import LegacyEconomicBenchmarkConfig
from pancake_prediction.legacy_economic_diagnostics import diagnose_legacy_pool_projection
from pancake_prediction.legacy_rounds import LegacyRoundRecord


def _round() -> LegacyRoundRecord:
    return LegacyRoundRecord(
        epoch=10,
        start_timestamp=1_000,
        lock_timestamp=1_300,
        close_timestamp=1_600,
        lock_price_e8=30_000_000_000,
        close_price_e8=30_100_000_000,
        lock_oracle_id=1,
        close_oracle_id=2,
        total_amount_wei=1_000,
        bull_amount_wei=900,
        bear_amount_wei=100,
        reward_base_cal_amount_wei=900,
        reward_amount_wei=970,
        oracle_called=True,
    )


def _config() -> LegacyEconomicBenchmarkConfig:
    return LegacyEconomicBenchmarkConfig(
        stake_wei=100,
        bet_gas_wei=0,
        claim_gas_wei=0,
        inclusion_latency_seconds=1,
        treasury_fee_bps=300,
        decision_lead_seconds=20,
        min_expected_value_wei=0,
        purge_rounds=2,
    )


def test_pool_projection_diagnostic_detects_ev_destroyed_by_final_pool_shift() -> None:
    signal = BacktestSignal(
        epoch=10,
        p_bull_ppm=700_000,
        generated_at=1_280,
        model_id="fixture",
        train_max_epoch=7,
    )
    projection = PoolProjection(
        epoch=10,
        generated_at=1_280,
        projected_bull_wei=100,
        projected_bear_wei=900,
        model_id="fixture-pool",
        train_max_epoch=7,
    )

    report = diagnose_legacy_pool_projection(
        (_round(),),
        {10: signal},
        {10: projection},
        _config(),
    )

    assert report.selected_trades == 1
    assert report.projected_positive_final_negative == 1
    assert report.projected_side_differs_from_final_pool_side == 1
    assert report.projected_ev_sum_wei > 0
    assert report.final_pool_ev_sum_wei < 0
    assert report.projected_ev_optimism_wei > 0
    assert report.mean_abs_bull_share_error_ppm == 800_000
    assert report.mean_abs_total_pool_error_ppm == 0
    assert report.diagnostic_uses_final_pool is True
    assert report.tradeable_feature is False
    assert report.profitability_gate_eligible is False


def test_pool_projection_diagnostic_skips_non_positive_projected_ev() -> None:
    signal = BacktestSignal(
        epoch=10,
        p_bull_ppm=500_000,
        generated_at=1_280,
        model_id="fixture",
        train_max_epoch=7,
    )
    projection = PoolProjection(
        epoch=10,
        generated_at=1_280,
        projected_bull_wei=500,
        projected_bear_wei=500,
        model_id="fixture-pool",
        train_max_epoch=7,
    )

    report = diagnose_legacy_pool_projection(
        (_round(),),
        {10: signal},
        {10: projection},
        _config(),
    )

    assert report.selected_trades == 0
    assert report.mean_abs_bull_share_error_ppm is None
    assert report.mean_abs_total_pool_error_ppm is None
