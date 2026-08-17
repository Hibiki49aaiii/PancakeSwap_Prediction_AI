from __future__ import annotations

import pytest

import pancake_prediction.features as feature_module
from pancake_prediction.backtest import BacktestConfig
from pancake_prediction.features import build_pool_feature_row, build_pool_feature_rows
from pancake_prediction.replay import ReplaySnapshot, RoundRecord


def _round(epoch: int, *, start: int) -> RoundRecord:
    bull = epoch % 2 == 0
    lock_price = 60_000_000_000
    close_price = lock_price + 100_000_000 if bull else lock_price - 100_000_000
    return RoundRecord(
        epoch=epoch,
        start_block=epoch * 10,
        start_timestamp=start,
        lock_block=epoch * 10 + 1,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=lock_price,
        end_block=epoch * 10 + 2,
        end_timestamp=start + 600,
        close_round_id=epoch + 1,
        close_price=close_price,
        bull_amount_wei=0,
        bear_amount_wei=0,
        total_amount_wei=0,
        bet_count=0,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label="bull" if bull else "bear",
        issues=(),
    )


def _replay(count: int = 40) -> ReplaySnapshot:
    return ReplaySnapshot(
        1,
        "BNBUSD",
        "b" * 64,
        tuple(_round(epoch, start=1_000 + epoch * 300) for epoch in range(1, count + 1)),
    )


def test_incremental_pool_history_matches_single_round_reference() -> None:
    replay = _replay()
    config = BacktestConfig()
    actual = build_pool_feature_rows(replay, (), config)
    expected = tuple(
        row
        for record in replay.rounds
        if (row := build_pool_feature_row(replay, record, (), config)) is not None
    )
    assert actual == expected


def test_monotonic_replay_avoids_quadratic_history_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay()

    def fail_scan(*args: object, **kwargs: object) -> tuple[int | None, int | None]:
        del args, kwargs
        raise AssertionError("quadratic history scan should not run for monotonic replay")

    monkeypatch.setattr(feature_module, "_known_history_features", fail_scan)
    rows = build_pool_feature_rows(replay, (), BacktestConfig())
    assert len(rows) == len(replay.rounds)


def test_unsorted_replay_falls_back_to_reference_path() -> None:
    ordered = _replay(3)
    replay = ReplaySnapshot(
        ordered.format_version,
        ordered.market,
        ordered.input_digest,
        (ordered.rounds[1], ordered.rounds[0], ordered.rounds[2]),
    )
    config = BacktestConfig()
    actual = build_pool_feature_rows(replay, (), config)
    expected = tuple(
        row
        for record in replay.rounds
        if (row := build_pool_feature_row(replay, record, (), config)) is not None
    )
    assert actual == expected
