from __future__ import annotations

from pancake_prediction.backtest import PoolProjection
from pancake_prediction.recent_benchmark import (
    RecentCanonicalEconomicConfig,
    run_recent_canonical_economic_benchmark,
)
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord
from pancake_prediction.walkforward import OosSignal


def _round(epoch: int, *, label: str = "bull") -> RoundRecord:
    start = 1_000 + epoch * 300
    lock = start + 300
    end = lock + 300
    lock_price = 30_000_000_000
    return RoundRecord(
        epoch=epoch,
        start_block=100 + epoch,
        start_timestamp=start,
        lock_block=200 + epoch,
        lock_timestamp=lock,
        lock_round_id=1_000 + epoch,
        lock_price=lock_price,
        end_block=300 + epoch,
        end_timestamp=end,
        close_round_id=2_000 + epoch,
        close_price=lock_price + (100_000_000 if label == "bull" else -100_000_000),
        bull_amount_wei=1_000,
        bear_amount_wei=1_000,
        total_amount_wei=2_000,
        bet_count=10,
        reward_base_cal_amount_wei=1_000,
        reward_amount_wei=1_940,
        treasury_amount_wei=60,
        label=label,
        issues=(),
    )


def _start_event(record: RoundRecord) -> ChainEvent:
    assert record.start_block is not None
    assert record.start_timestamp is not None
    return ChainEvent(
        block_number=record.start_block,
        block_hash=f"0x{record.start_block:064x}",
        block_timestamp=record.start_timestamp,
        tx_hash=f"0x{record.epoch:064x}",
        tx_index=0,
        log_index=0,
        event_name="StartRound",
        decoded={"epoch": record.epoch},
    )


def _fixture() -> tuple[ReplaySnapshot, tuple[ChainEvent, ...]]:
    record = _round(10)
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(record,),
    )
    return replay, (_start_event(record),)


def _signal(epoch: int, generated_at: int, *, train_max: int = 7) -> OosSignal:
    return OosSignal(
        epoch=epoch,
        p_bull_ppm=900_000,
        generated_at=generated_at,
        train_max_epoch=train_max,
        fold="recent-cex-wf-1",
    )


def _projection(epoch: int, generated_at: int, *, train_max: int = 7) -> PoolProjection:
    return PoolProjection(
        epoch=epoch,
        generated_at=generated_at,
        projected_bull_wei=1_000,
        projected_bear_wei=1_000,
        model_id="prior-final-test",
        train_max_epoch=train_max,
    )


def _config(*, latency: int = 3) -> RecentCanonicalEconomicConfig:
    return RecentCanonicalEconomicConfig(
        stake_wei=100,
        bet_gas_wei=2,
        claim_gas_wei=1,
        inclusion_latency_seconds=latency,
        treasury_fee_bps=300,
        decision_lead_seconds=20,
        purge_rounds=2,
    )


def test_recent_canonical_economics_uses_exact_final_pool_and_explicit_costs() -> None:
    replay, events = _fixture()
    record = replay.rounds[0]
    assert record.start_timestamp is not None
    decision = record.start_timestamp + 280
    report = run_recent_canonical_economic_benchmark(
        replay,
        events,
        {10: _signal(10, decision)},
        {10: _projection(10, decision)},
        _config(),
    )

    assert report.authoritative_prediction_events is True
    assert report.chainlink_collected is False
    assert report.profitability_gate_eligible is False
    assert report.trade_count == 1
    assert report.trades[0].side == "bull"
    assert report.trades[0].final_bull_wei == 1_000
    assert report.trades[0].final_bear_wei == 1_000
    assert report.pnl_wei == 82
    assert report.roi_ppm == 820_000


def test_recent_canonical_economics_rejects_unpurged_or_late_submission() -> None:
    replay, events = _fixture()
    record = replay.rounds[0]
    assert record.start_timestamp is not None
    decision = record.start_timestamp + 280
    unpurged = run_recent_canonical_economic_benchmark(
        replay,
        events,
        {10: _signal(10, decision, train_max=8)},
        {10: _projection(10, decision)},
        _config(),
    )
    assert unpurged.trade_count == 0
    assert unpurged.skipped_oos_provenance == 1

    late = run_recent_canonical_economic_benchmark(
        replay,
        events,
        {10: _signal(10, decision)},
        {10: _projection(10, decision)},
        _config(latency=20),
    )
    assert late.trade_count == 0
    assert late.skipped_late == 1


def test_recent_canonical_economics_skips_missing_signal_without_fabrication() -> None:
    replay, events = _fixture()
    record = replay.rounds[0]
    assert record.start_timestamp is not None
    decision = record.start_timestamp + 280
    report = run_recent_canonical_economic_benchmark(
        replay,
        events,
        {},
        {10: _projection(10, decision)},
        _config(),
    )

    assert report.trade_count == 0
    assert report.skipped_missing_signal == 1
