from __future__ import annotations

from dataclasses import replace

from pancake_prediction.absolute_pool_projection import AbsolutePoolProjectionConfig
from pancake_prediction.legacy_pool_projection import build_legacy_absolute_pool_projections
from pancake_prediction.legacy_rounds import LegacyRoundRecord


def _round(
    epoch: int,
    *,
    start: int,
    lock: int,
    close: int,
    bull: int,
    bear: int,
    oracle_called: bool = True,
) -> LegacyRoundRecord:
    return LegacyRoundRecord(
        epoch=epoch,
        start_timestamp=start,
        lock_timestamp=lock,
        close_timestamp=close,
        lock_price_e8=30_000_000_000,
        close_price_e8=30_100_000_000,
        lock_oracle_id=10_000 + epoch,
        close_oracle_id=20_000 + epoch,
        total_amount_wei=bull + bear,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        reward_base_cal_amount_wei=bull,
        reward_amount_wei=(bull + bear) * 97 // 100,
        oracle_called=oracle_called,
    )


def _fixture() -> tuple[LegacyRoundRecord, ...]:
    return (
        _round(1, start=100, lock=400, close=700, bull=600, bear=400),
        _round(2, start=400, lock=700, close=900, bull=800, bear=200),
        _round(3, start=700, lock=1_000, close=1_300, bull=9_000, bear=1_000),
        _round(4, start=1_000, lock=1_300, close=1_600, bull=700, bear=300),
    )


def test_legacy_pool_projection_does_not_read_target_final_pool() -> None:
    rounds = _fixture()
    config = AbsolutePoolProjectionConfig(
        min_train_rounds=2,
        window_rounds=2,
        purge_rounds=0,
    )
    original = build_legacy_absolute_pool_projections(
        rounds,
        decision_lead_seconds=20,
        config=config,
    )
    target = rounds[2]
    mutated_target = replace(
        target,
        bull_amount_wei=999_999_999,
        bear_amount_wei=888_888_888,
        total_amount_wei=1_888_888_887,
        reward_base_cal_amount_wei=999_999_999,
        reward_amount_wei=1_832_222_220,
    )
    mutated = build_legacy_absolute_pool_projections(
        (rounds[0], rounds[1], mutated_target, rounds[3]),
        decision_lead_seconds=20,
        config=config,
    )

    assert original[3] == mutated[3]
    assert original[3].projected_bull_wei == 700
    assert original[3].projected_bear_wei == 300
    assert original[3].train_max_epoch == 2


def test_legacy_pool_projection_obeys_close_time_and_purge() -> None:
    rounds = _fixture()
    late_prior = replace(rounds[1], close_timestamp=990)
    projections = build_legacy_absolute_pool_projections(
        (rounds[0], late_prior, rounds[2], rounds[3]),
        decision_lead_seconds=20,
        config=AbsolutePoolProjectionConfig(
            min_train_rounds=1,
            window_rounds=10,
            purge_rounds=1,
        ),
    )

    assert 2 not in projections
    assert projections[3].train_max_epoch == 1
    assert projections[4].train_max_epoch == 2


def test_legacy_pool_projection_excludes_refunded_prior_rounds() -> None:
    rounds = _fixture()
    refunded = replace(rounds[1], oracle_called=False)
    projections = build_legacy_absolute_pool_projections(
        (rounds[0], refunded, rounds[2]),
        decision_lead_seconds=20,
        config=AbsolutePoolProjectionConfig(
            min_train_rounds=1,
            window_rounds=10,
            purge_rounds=0,
        ),
    )

    assert projections[3].train_max_epoch == 1
    assert projections[3].projected_bull_wei == 600
    assert projections[3].projected_bear_wei == 400
