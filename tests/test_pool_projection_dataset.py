from __future__ import annotations

from pancake_prediction.backtest import BacktestConfig
from pancake_prediction.pool_projection_dataset import build_pool_projection_dataset
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


def _round(epoch: int, *, start: int, bull: int, bear: int) -> RoundRecord:
    return RoundRecord(
        epoch=epoch,
        start_block=epoch * 10,
        start_timestamp=start,
        lock_block=epoch * 10 + 1,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=100,
        end_block=epoch * 10 + 2,
        end_timestamp=start + 600,
        close_round_id=epoch + 1,
        close_price=101,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        total_amount_wei=bull + bear,
        bet_count=0,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label="bull",
        issues=(),
    )


def _bet(epoch: int, timestamp: int, amount: int, side: str, index: int) -> ChainEvent:
    return ChainEvent(
        block_number=100 + index,
        block_hash="0x" + f"{100 + index:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{index + 1:064x}",
        tx_index=0,
        log_index=index,
        event_name=side,
        decoded={
            "epoch": epoch,
            "amount": amount,
            "sender": "0x" + f"{index + 1:040x}",
        },
    )


def _fixture() -> tuple[ReplaySnapshot, tuple[ChainEvent, ...]]:
    record = _round(10, start=1_000, bull=1_000, bear=800)
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, (record,))
    events = (
        _bet(10, 1_210, 100, "BetBull", 1),
        _bet(10, 1_260, 200, "BetBear", 2),
        _bet(10, 1_275, 300, "BetBull", 3),
        _bet(10, 1_279, 400, "BetBear", 4),
        _bet(10, 1_280, 999, "BetBull", 5),
    )
    return replay, events


def test_pool_projection_dataset_is_cutoff_safe_and_windowed() -> None:
    replay, events = _fixture()
    dataset = build_pool_projection_dataset(
        replay,
        events,
        BacktestConfig(),
        feature_lead_seconds=20,
    )

    assert len(dataset.rows) == 1
    row = dataset.rows[0]
    features = row.features
    assert features.decision_timestamp == 1_280
    assert features.observed_bull_wei == 400
    assert features.observed_bear_wei == 600
    assert features.bull_flow_5s_wei == 300
    assert features.bear_flow_5s_wei == 400
    assert features.bull_flow_20s_wei == 300
    assert features.bear_flow_20s_wei == 600
    assert features.bull_flow_60s_wei == 300
    assert features.bear_flow_60s_wei == 600
    assert row.label.final_bull_wei == 1_000
    assert row.label.final_bear_wei == 800


def test_final_pool_is_label_only_and_dataset_digest_is_deterministic() -> None:
    replay, events = _fixture()
    first = build_pool_projection_dataset(replay, events, BacktestConfig())
    second = build_pool_projection_dataset(replay, tuple(reversed(events)), BacktestConfig())

    assert first.dataset_digest == second.dataset_digest
    assert first.as_dict()["final_pool_is_label_only"] is True
    feature_payload = first.rows[0].features.as_dict()
    assert "final_bull_wei" not in feature_payload
    assert "final_bear_wei" not in feature_payload
    assert len(first.dataset_digest) == 64


def test_dataset_rejects_final_pool_smaller_than_observed_cutoff_pool() -> None:
    record = _round(10, start=1_000, bull=100, bear=100)
    replay = ReplaySnapshot(1, "BNBUSD", "b" * 64, (record,))
    events = (_bet(10, 1_270, 200, "BetBull", 1),)

    try:
        build_pool_projection_dataset(replay, events, BacktestConfig())
    except ValueError as exc:
        assert "smaller than the decision-time observed pool" in str(exc)
    else:
        raise AssertionError("inconsistent final pool must fail closed")
