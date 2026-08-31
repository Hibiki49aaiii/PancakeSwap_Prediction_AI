from pancake_prediction.backtest import BacktestConfig, build_decision_snapshot
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


def _round() -> RoundRecord:
    return RoundRecord(
        epoch=10,
        start_block=100,
        start_timestamp=1000,
        lock_block=200,
        lock_timestamp=1400,
        lock_round_id=1,
        lock_price=100,
        end_block=300,
        end_timestamp=1600,
        close_round_id=2,
        close_price=101,
        bull_amount_wei=1000,
        bear_amount_wei=1000,
        total_amount_wei=2000,
        bet_count=2,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label="bull",
        issues=(),
    )


def _event(log_index: int, name: str, decoded: dict[str, object]) -> ChainEvent:
    return ChainEvent(
        block_number=100,
        block_hash="0x" + f"{100:064x}",
        block_timestamp=1000,
        tx_hash="0x" + f"{10000 + log_index:064x}",
        tx_index=0,
        log_index=log_index,
        event_name=name,
        decoded=decoded,
    )


def test_same_block_protocol_update_uses_exact_log_order() -> None:
    record = _round()
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, (record,))
    before = _event(
        0,
        "NewBufferAndIntervalSeconds",
        {"bufferSeconds": 30, "intervalSeconds": 400},
    )
    start = _event(1, "StartRound", {"epoch": 10})
    after = _event(
        2,
        "NewBufferAndIntervalSeconds",
        {"bufferSeconds": 30, "intervalSeconds": 500},
    )

    snapshot = build_decision_snapshot(
        replay,
        record,
        (after, start, before),
        BacktestConfig(decision_lead_seconds=20),
    )

    assert snapshot is not None
    assert snapshot.interval_seconds == 400
    assert snapshot.scheduled_lock_timestamp == 1400
    assert snapshot.decision_timestamp == 1380
