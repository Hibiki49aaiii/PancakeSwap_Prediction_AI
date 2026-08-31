from dataclasses import replace

import pytest

from pancake_prediction.backtest import (
    BacktestConfig,
    BacktestSignal,
    PoolProjection,
    build_decision_snapshot,
    run_backtest,
)
from pancake_prediction.economics import ParimutuelQuote, gross_payout_if_win_wei
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


def _event(
    *, epoch: int, timestamp: int, block: int, log_index: int, name: str, amount: int | None = None
) -> ChainEvent:
    decoded: dict[str, object] = {"epoch": epoch}
    if amount is not None:
        decoded["amount"] = amount
        decoded["sender"] = "0x" + f"{log_index + 1:040x}"
    return ChainEvent(
        block_number=block,
        block_hash="0x" + f"{block:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{block * 100 + log_index:064x}",
        tx_index=0,
        log_index=log_index,
        event_name=name,
        decoded=decoded,
    )


def _round(
    *,
    epoch: int = 10,
    label: str = "bull",
    bull: int = 1000,
    bear: int = 1000,
    start: int = 1000,
    actual_lock: int = 1400,
) -> RoundRecord:
    return RoundRecord(
        epoch=epoch,
        start_block=100,
        start_timestamp=start,
        lock_block=200,
        lock_timestamp=actual_lock,
        lock_round_id=1,
        lock_price=100,
        end_block=300,
        end_timestamp=1600,
        close_round_id=2,
        close_price=101 if label == "bull" else 99,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        total_amount_wei=bull + bear,
        bet_count=2,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label=label,
        issues=(),
    )


def _replay(record: RoundRecord) -> ReplaySnapshot:
    return ReplaySnapshot(1, "BNBUSD", "a" * 64, (record,))


def test_decision_uses_scheduled_lock_and_excludes_cutoff_block() -> None:
    record = _round(actual_lock=1450)
    replay = _replay(record)
    events = (
        _event(epoch=10, timestamp=1200, block=110, log_index=0, name="BetBull", amount=100),
        _event(epoch=10, timestamp=1280, block=111, log_index=0, name="BetBear", amount=200),
        _event(epoch=10, timestamp=1290, block=112, log_index=0, name="BetBear", amount=300),
    )
    config = BacktestConfig(decision_lead_seconds=20)
    snapshot = build_decision_snapshot(replay, record, events, config)
    assert snapshot is not None
    assert snapshot.scheduled_lock_timestamp == 1300
    assert snapshot.decision_timestamp == 1280
    assert snapshot.bull_observed_wei == 100
    assert snapshot.bear_observed_wei == 0


def test_backtest_rejects_future_pool_projection() -> None:
    record = _round()
    replay = _replay(record)
    config = BacktestConfig(decision_lead_seconds=20)
    signals = {10: BacktestSignal(10, 800_000, 1270, "model")}
    projections = {10: PoolProjection(10, 1281, 1000, 1000, "pool-model")}
    with pytest.raises(ValueError, match="after the decision cutoff"):
        run_backtest(replay, (), signals, projections, config)


def test_backtest_requires_projection_by_default() -> None:
    record = _round()
    report = run_backtest(
        _replay(record),
        (),
        {10: BacktestSignal(10, 800_000, 1270, "model")},
        {},
        BacktestConfig(),
    )
    assert report.trades == ()
    assert report.skipped_no_projection == 1


def test_realized_pnl_uses_final_pool_not_projection() -> None:
    record = _round(bull=9_000, bear=1_000, label="bull")
    replay = _replay(record)
    config = BacktestConfig(stake_wei=100, decision_lead_seconds=20)
    signal = BacktestSignal(10, 900_000, 1270, "model")
    projection = PoolProjection(10, 1270, 1000, 1000, "pool-model")
    report = run_backtest(replay, (), {10: signal}, {10: projection}, config)
    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.side == "bull"
    assert trade.final_bull_wei == 9_000
    realized_quote = ParimutuelQuote("bull", 9_000, 1_000, 100, 300)
    expected_pnl = gross_payout_if_win_wei(realized_quote) - 100
    assert trade.pnl_wei == expected_pnl


def test_latency_is_checked_against_scheduled_lock() -> None:
    record = _round(actual_lock=1500)
    replay = _replay(record)
    config = BacktestConfig(decision_lead_seconds=2, inclusion_latency_seconds=3)
    signal = BacktestSignal(10, 900_000, 1298, "model")
    projection = PoolProjection(10, 1298, 1000, 1000, "pool")
    report = run_backtest(replay, (), {10: signal}, {10: projection}, config)
    assert report.trades == ()
    assert report.skipped_late == 1


def test_rewards_mismatch_excludes_round_from_profit_calculation() -> None:
    base = _round(bull=600, bear=400, label="bull")
    bad = replace(
        base,
        reward_base_cal_amount_wei=600,
        reward_amount_wei=999,
        treasury_amount_wei=1,
    )
    report = run_backtest(
        _replay(bad),
        (),
        {10: BacktestSignal(10, 900_000, 1270, "model")},
        {10: PoolProjection(10, 1270, 600, 400, "pool")},
        BacktestConfig(),
    )
    assert report.trades == ()
    assert report.skipped_integrity == 1
