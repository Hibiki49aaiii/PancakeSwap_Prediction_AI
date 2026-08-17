import pytest

from pancake_prediction.alignment import (
    AvailablePrice,
    build_aligned_alpha_feature_row,
    build_aligned_alpha_inputs,
    latest_available_price,
)
from pancake_prediction.binance import AggTrade
from pancake_prediction.replay import ChainEvent


def _trade(
    trade_id: int,
    *,
    trade_timestamp_ms: int,
    event_timestamp_ms: int,
    price_e8: int,
    side: str = "buy",
) -> AggTrade:
    return AggTrade(
        symbol="BNBUSDT",
        event_timestamp_ms=event_timestamp_ms,
        trade_timestamp_ms=trade_timestamp_ms,
        price_e8=price_e8,
        quantity_e8=100_000_000,
        aggressive_side=side,
        aggregate_trade_id=trade_id,
    )


def _chainlink_event(*, block_timestamp: int, updated_at: int, price_e8: int) -> ChainEvent:
    return ChainEvent(
        block_number=1,
        block_hash="0xabc",
        block_timestamp=block_timestamp,
        tx_hash="0xdef",
        tx_index=0,
        log_index=0,
        event_name="AnswerUpdated",
        decoded={"current": price_e8, "roundId": 1, "updatedAt": updated_at},
    )


def test_latest_price_rejects_observation_not_available_before_cutoff() -> None:
    observations = (
        AvailablePrice("spot", 9_000, 9_100, 100),
        AvailablePrice("spot", 9_900, 10_100, 101),
    )
    selected = latest_available_price(observations, decision_timestamp_ms=10_000)
    assert selected is not None
    assert selected.price_e8 == 100


def test_latest_price_rejects_conflicting_same_timestamp_values() -> None:
    observations = (
        AvailablePrice("spot", 9_900, 9_950, 100),
        AvailablePrice("spot", 9_900, 9_950, 101),
    )
    with pytest.raises(ValueError, match="conflicting prices"):
        latest_available_price(observations, decision_timestamp_ms=10_000)


def test_alignment_excludes_trade_published_after_decision() -> None:
    decision = 100_000
    spot = (
        _trade(
            1,
            trade_timestamp_ms=99_000,
            event_timestamp_ms=99_010,
            price_e8=60_000_000_000,
        ),
        _trade(
            2,
            trade_timestamp_ms=99_900,
            event_timestamp_ms=100_010,
            price_e8=61_000_000_000,
        ),
    )
    chainlink = (
        _chainlink_event(
            block_timestamp=99,
            updated_at=98,
            price_e8=60_000_000_000,
        ),
    )
    aligned = build_aligned_alpha_inputs(
        decision_timestamp_ms=decision,
        chainlink_events=chainlink,
        spot_trades=spot,
        flow_lookback_ms=10_000,
        max_spot_age_ms=5_000,
    )
    assert aligned is not None
    assert aligned.spot.price_e8 == 60_000_000_000
    assert aligned.spot_flow.trade_count == 1


def test_chainlink_update_is_not_available_before_its_block() -> None:
    decision = 100_000
    spot = (
        _trade(
            1,
            trade_timestamp_ms=99_000,
            event_timestamp_ms=99_010,
            price_e8=60_000_000_000,
        ),
    )
    chainlink = (
        _chainlink_event(
            block_timestamp=101,
            updated_at=99,
            price_e8=60_000_000_000,
        ),
    )
    aligned = build_aligned_alpha_inputs(
        decision_timestamp_ms=decision,
        chainlink_events=chainlink,
        spot_trades=spot,
        flow_lookback_ms=10_000,
    )
    assert aligned is None


def test_aligned_inputs_feed_alpha_without_future_data() -> None:
    decision = 100_000
    spot = (
        _trade(
            1,
            trade_timestamp_ms=99_000,
            event_timestamp_ms=99_010,
            price_e8=60_100_000_000,
        ),
    )
    perp = (
        _trade(
            2,
            trade_timestamp_ms=99_100,
            event_timestamp_ms=99_120,
            price_e8=60_200_000_000,
            side="sell",
        ),
    )
    chainlink = tuple(
        _chainlink_event(
            block_timestamp=90 + index,
            updated_at=89 + index,
            price_e8=60_000_000_000 + index,
        )
        for index in range(10)
    )
    aligned = build_aligned_alpha_inputs(
        decision_timestamp_ms=decision,
        chainlink_events=chainlink,
        spot_trades=spot,
        perp_trades=perp,
        flow_lookback_ms=20_000,
        max_spot_age_ms=5_000,
        max_perp_age_ms=5_000,
    )
    assert aligned is not None
    feature = build_aligned_alpha_feature_row(
        market="BNBUSD",
        epoch=123,
        aligned=aligned,
        oracle_hazard_min_intervals=2,
    )
    assert feature.epoch == 123
    assert feature.spot_observed_at_ms < decision
    assert feature.perp_observed_at_ms is not None
    assert feature.perp_observed_at_ms < decision
