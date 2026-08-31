from __future__ import annotations

from dataclasses import replace

from pancake_prediction.absolute_pool_projection import (
    AbsolutePoolProjectionConfig,
    build_prior_settled_absolute_pool_projections,
)
from pancake_prediction.backtest import BacktestConfig
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


def _round(epoch: int, *, bull: int, bear: int) -> RoundRecord:
    start = 1_000 + (epoch - 1) * 300
    end = start + 300
    return RoundRecord(
        epoch=epoch,
        start_block=100 + epoch,
        start_timestamp=start,
        lock_block=110 + epoch,
        lock_timestamp=end,
        lock_round_id=1_000 + epoch,
        lock_price=30_000_000_000,
        end_block=120 + epoch,
        end_timestamp=end,
        close_round_id=2_000 + epoch,
        close_price=30_100_000_000,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        total_amount_wei=bull + bear,
        bet_count=10,
        reward_base_cal_amount_wei=bull,
        reward_amount_wei=(bull + bear) * 97 // 100,
        treasury_amount_wei=(bull + bear) * 3 // 100,
        label="bull",
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
    rounds = (
        _round(1, bull=600, bear=400),
        _round(2, bull=800, bear=200),
        _round(3, bull=9_000, bear=1_000),
        _round(4, bull=700, bear=300),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=rounds,
    )
    return replay, tuple(_start_event(record) for record in rounds)


def test_absolute_pool_projection_never_reads_target_pool() -> None:
    replay, events = _fixture()
    config = AbsolutePoolProjectionConfig(
        min_train_rounds=2,
        window_rounds=2,
        purge_rounds=0,
    )
    backtest = BacktestConfig(decision_lead_seconds=20)
    original = build_prior_settled_absolute_pool_projections(
        replay,
        events,
        backtest,
        config=config,
    )

    target = replay.rounds[2]
    mutated_target = replace(
        target,
        bull_amount_wei=999_999_999,
        bear_amount_wei=888_888_888,
        total_amount_wei=1_888_888_887,
        reward_base_cal_amount_wei=999_999_999,
        reward_amount_wei=1_832_222_220,
        treasury_amount_wei=56_666_667,
    )
    mutated = replace(
        replay,
        rounds=(replay.rounds[0], replay.rounds[1], mutated_target, replay.rounds[3]),
    )
    changed = build_prior_settled_absolute_pool_projections(
        mutated,
        events,
        backtest,
        config=config,
    )

    assert original[3] == changed[3]
    assert original[3].projected_bull_wei == 700
    assert original[3].projected_bear_wei == 300
    assert original[3].train_max_epoch == 2


def test_absolute_pool_projection_obeys_settlement_time_and_purge() -> None:
    replay, events = _fixture()
    projections = build_prior_settled_absolute_pool_projections(
        replay,
        events,
        BacktestConfig(decision_lead_seconds=20),
        config=AbsolutePoolProjectionConfig(
            min_train_rounds=1,
            window_rounds=10,
            purge_rounds=1,
        ),
    )

    assert 2 not in projections
    assert projections[3].train_max_epoch == 1
    assert projections[4].train_max_epoch == 2
