from dataclasses import replace

import pytest

from pancake_prediction.backtest import BacktestConfig
from pancake_prediction.pool_projection import (
    PoolProjectionBaselineConfig,
    build_oos_pool_projection_for_target,
    build_oos_pool_projections,
)
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


def _round(epoch: int, *, bull_final: int, bear_final: int) -> RoundRecord:
    start = epoch * 1_000
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
        close_price=101 if epoch % 2 else 99,
        bull_amount_wei=bull_final,
        bear_amount_wei=bear_final,
        total_amount_wei=bull_final + bear_final,
        bet_count=4,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label="bull" if epoch % 2 else "bear",
        issues=(),
    )


def _bet(
    epoch: int,
    *,
    timestamp: int,
    amount: int,
    side: str,
    index: int,
) -> ChainEvent:
    return ChainEvent(
        block_number=epoch * 10,
        block_hash="0x" + f"{epoch * 10:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{epoch * 100 + index:064x}",
        tx_index=0,
        log_index=index,
        event_name=side,
        decoded={
            "epoch": epoch,
            "amount": amount,
            "sender": "0x" + f"{index + 1:040x}",
        },
    )


def _fixture(
    target_bull: int,
    target_bear: int,
) -> tuple[ReplaySnapshot, tuple[ChainEvent, ...]]:
    rounds = tuple(
        _round(
            epoch,
            bull_final=(160 if epoch < 8 else target_bull),
            bear_final=(140 if epoch < 8 else target_bear),
        )
        for epoch in range(1, 9)
    )
    events: list[ChainEvent] = []
    for epoch in range(1, 9):
        start = epoch * 1_000
        events.extend(
            (
                _bet(
                    epoch,
                    timestamp=start + 100,
                    amount=100,
                    side="BetBull",
                    index=0,
                ),
                _bet(
                    epoch,
                    timestamp=start + 120,
                    amount=100,
                    side="BetBear",
                    index=1,
                ),
            )
        )
    return ReplaySnapshot(1, "BNBUSD", "a" * 64, rounds), tuple(events)


def test_target_final_pool_cannot_change_its_own_projection() -> None:
    first_replay, events = _fixture(1_000, 100)
    second_replay = replace(
        first_replay,
        rounds=(
            *first_replay.rounds[:-1],
            replace(
                first_replay.rounds[-1],
                bull_amount_wei=10,
                bear_amount_wei=9_000,
            ),
        ),
    )
    config = PoolProjectionBaselineConfig(
        min_train_rounds=3,
        window_rounds=10,
        purge_rounds=2,
    )
    first = build_oos_pool_projections(
        first_replay,
        events,
        BacktestConfig(),
        config=config,
    )[8]
    second = build_oos_pool_projections(
        second_replay,
        events,
        BacktestConfig(),
        config=config,
    )[8]
    assert first == second


def test_projection_provenance_respects_purge_boundary() -> None:
    replay, events = _fixture(1_000, 100)
    config = PoolProjectionBaselineConfig(
        min_train_rounds=3,
        window_rounds=10,
        purge_rounds=2,
    )
    projection = build_oos_pool_projections(
        replay,
        events,
        BacktestConfig(),
        config=config,
    )[8]
    assert projection.train_max_epoch is not None
    assert projection.train_max_epoch <= 5
    assert projection.generated_at == 8_280
    assert projection.projected_bull_wei >= 100
    assert projection.projected_bear_wei >= 100

def test_single_target_projection_matches_full_oos_builder_exactly() -> None:
    replay, events = _fixture(1_000, 100)
    config = PoolProjectionBaselineConfig(
        min_train_rounds=3,
        window_rounds=10,
        purge_rounds=2,
    )
    backtest = BacktestConfig()

    full = build_oos_pool_projections(
        replay,
        events,
        backtest,
        config=config,
    )[8]
    single = build_oos_pool_projection_for_target(
        replay,
        events,
        backtest,
        target_epoch=8,
        config=config,
    )

    assert single == full
    assert single is not None
    assert single.model_id == full.model_id
    assert single.train_max_epoch == full.train_max_epoch


def test_single_target_projection_ignores_target_final_pool() -> None:
    first_replay, events = _fixture(1_000, 100)
    changed_replay = replace(
        first_replay,
        rounds=(
            *first_replay.rounds[:-1],
            replace(
                first_replay.rounds[-1],
                bull_amount_wei=10,
                bear_amount_wei=9_000,
                total_amount_wei=9_010,
            ),
        ),
    )
    config = PoolProjectionBaselineConfig(
        min_train_rounds=3,
        window_rounds=10,
        purge_rounds=2,
    )
    backtest = BacktestConfig()

    first = build_oos_pool_projection_for_target(
        first_replay,
        events,
        backtest,
        target_epoch=8,
        config=config,
    )
    second = build_oos_pool_projection_for_target(
        changed_replay,
        events,
        backtest,
        target_epoch=8,
        config=config,
    )

    assert first == second


def test_single_target_projection_requires_exactly_one_target_epoch() -> None:
    replay, events = _fixture(1_000, 100)
    config = PoolProjectionBaselineConfig(
        min_train_rounds=3,
        window_rounds=10,
        purge_rounds=2,
    )
    with pytest.raises(ValueError, match="must appear exactly once"):
        build_oos_pool_projection_for_target(
            replay,
            events,
            BacktestConfig(),
            target_epoch=999,
            config=config,
        )

    duplicate = replace(
        replay,
        rounds=(*replay.rounds, replay.rounds[-1]),
    )
    with pytest.raises(ValueError, match="must appear exactly once"):
        build_oos_pool_projection_for_target(
            duplicate,
            events,
            BacktestConfig(),
            target_epoch=8,
            config=config,
        )

