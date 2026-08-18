from __future__ import annotations

from pancake_prediction.backtest import BacktestSignal, PoolProjection
from pancake_prediction.legacy_benchmark import (
    LegacyEconomicBenchmarkConfig,
    run_legacy_economic_benchmark,
)
from pancake_prediction.legacy_rounds import LegacyRoundRecord


def _round(
    epoch: int,
    *,
    label: str = "bull",
    oracle_called: bool = True,
    bull: int = 1_000,
    bear: int = 1_000,
) -> LegacyRoundRecord:
    lock_price = 30_000_000_000
    close_price = 30_100_000_000 if label == "bull" else 29_900_000_000
    if label == "tie":
        close_price = lock_price
    reward_base = bull if label == "bull" else bear
    return LegacyRoundRecord(
        epoch=epoch,
        start_timestamp=1_000,
        lock_timestamp=1_300,
        close_timestamp=1_600,
        lock_price_e8=lock_price,
        close_price_e8=close_price,
        lock_oracle_id=100,
        close_oracle_id=101,
        total_amount_wei=bull + bear,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        reward_base_cal_amount_wei=reward_base,
        reward_amount_wei=(bull + bear) * 97 // 100,
        oracle_called=oracle_called,
    )


def _signal(epoch: int, *, train_max: int, probability: int = 900_000) -> BacktestSignal:
    return BacktestSignal(
        epoch=epoch,
        p_bull_ppm=probability,
        generated_at=1_280,
        model_id="oos-test",
        train_max_epoch=train_max,
    )


def _projection(epoch: int, *, train_max: int) -> PoolProjection:
    return PoolProjection(
        epoch=epoch,
        generated_at=1_280,
        projected_bull_wei=1_000,
        projected_bear_wei=1_000,
        model_id="prior-final-test",
        train_max_epoch=train_max,
    )


def _config() -> LegacyEconomicBenchmarkConfig:
    return LegacyEconomicBenchmarkConfig(
        stake_wei=100,
        bet_gas_wei=2,
        claim_gas_wei=1,
        inclusion_latency_seconds=3,
        treasury_fee_bps=300,
        decision_lead_seconds=20,
        purge_rounds=2,
    )


def test_legacy_benchmark_is_explicitly_non_authoritative_and_cost_aware() -> None:
    record = _round(10)
    report = run_legacy_economic_benchmark(
        (record,),
        {10: _signal(10, train_max=7)},
        {10: _projection(10, train_max=7)},
        _config(),
    )

    assert report.authoritative is False
    assert report.source_class == "third_party_historical_benchmark"
    assert report.trade_count == 1
    assert report.pnl_wei == 82
    assert report.capital_at_risk_wei == 100
    assert report.roi_ppm == 820_000
    assert report.trades[0].side == "bull"
    assert report.trades[0].pnl_wei == 82
    assert any("cannot pass" in item for item in report.limitations)


def test_legacy_benchmark_rejects_non_purged_signal_or_projection() -> None:
    record = _round(10)
    report = run_legacy_economic_benchmark(
        (record,),
        {10: _signal(10, train_max=8)},
        {10: _projection(10, train_max=7)},
        _config(),
    )
    assert report.trade_count == 0
    assert report.skipped_oos_provenance == 1

    report_projection = run_legacy_economic_benchmark(
        (record,),
        {10: _signal(10, train_max=7)},
        {10: _projection(10, train_max=8)},
        _config(),
    )
    assert report_projection.trade_count == 0
    assert report_projection.skipped_oos_provenance == 1


def test_legacy_benchmark_skips_refunds_empty_sides_and_late_execution() -> None:
    refunded = _round(10, oracle_called=False)
    empty = _round(11, bull=0, bear=2_000)
    late_config = LegacyEconomicBenchmarkConfig(
        stake_wei=100,
        bet_gas_wei=2,
        claim_gas_wei=1,
        inclusion_latency_seconds=20,
        decision_lead_seconds=20,
        purge_rounds=2,
    )
    normal = _round(12)
    report = run_legacy_economic_benchmark(
        (refunded, empty, normal),
        {12: _signal(12, train_max=9)},
        {12: _projection(12, train_max=9)},
        late_config,
    )

    assert report.skipped_refunded == 1
    assert report.skipped_empty_pool == 1
    assert report.skipped_late == 1
    assert report.trade_count == 0
