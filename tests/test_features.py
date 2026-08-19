from __future__ import annotations

import math

import pytest

from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.features import build_core_features
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.replay import build_snapshot
from pancake_prediction_ai.sources.binance import normalize_agg_trade, normalize_book_ticker
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
FEED = "0x1111111111111111111111111111111111111111"


def _agg(trade_id: int, *, trade_time_s: int, price: str, quantity: str, buyer_is_maker: bool):
    return normalize_agg_trade(
        {
            "e": "aggTrade",
            "E": trade_time_s * 1000 + 1,
            "s": "BNBUSDT",
            "a": trade_id,
            "p": price,
            "q": quantity,
            "f": trade_id,
            "l": trade_id,
            "T": trade_time_s * 1000,
            "m": buyer_is_maker,
        },
        observed_at_ns=(trade_time_s + 1) * 1_000_000_000,
    )


def _round() -> PredictionRoundState:
    bull = 100_000_000_000_000_000_000
    bear = 200_000_000_000_000_000_000
    return PredictionRoundState(
        epoch=9,
        start_timestamp=70,
        lock_timestamp=130,
        close_timestamp=190,
        lock_price=0,
        close_price=0,
        lock_oracle_id=0,
        close_oracle_id=0,
        total_amount_wei=bull + bear,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        reward_base_cal_amount_wei=0,
        reward_amount_wei=0,
        oracle_called=False,
    )


def test_core_features_combine_market_oracle_flow_and_pool_without_float_wei_loss(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(
            normalize_book_ticker(
                {"u": 1, "s": "BNBUSDT", "b": "599", "B": "10", "a": "601", "A": "12"},
                observed_at_ns=105_000_000_000,
            )
        )
        store.append(
            normalize_latest_round_data(
                (10, 598_00000000, 99, 100, 10),
                decimals=8,
                feed_address=FEED,
                observed_at_ns=101_000_000_000,
            )
        )
        store.append(
            normalize_round_snapshot(
                _round(),
                contract_address=PREDICTION,
                treasury_fee_units=300,
                block_number=1_000,
                block_timestamp_s=104,
                observed_at_ns=105_000_000_000,
            )
        )
        store.append(_agg(1, trade_time_s=90, price="599", quantity="100", buyer_is_maker=False))
        store.append(_agg(2, trade_time_s=100, price="600", quantity="2", buyer_is_maker=False))
        store.append(_agg(3, trade_time_s=105, price="601", quantity="1", buyer_is_maker=True))
        events = store.read_all_ingest_order()

    snapshot = build_snapshot(events, cutoff_ns=110_000_000_000)
    features = build_core_features(snapshot, trade_window_ns=15_000_000_000)

    assert features.binance_mid_price == pytest.approx(600.0)
    assert features.binance_spread_bps == pytest.approx(2 / 600 * 10_000)
    assert features.chainlink_price == pytest.approx(598.0)
    assert features.binance_chainlink_divergence_bps == pytest.approx((600 - 598) / 598 * 10_000)
    assert features.oracle_age_seconds == pytest.approx(10.0)
    assert features.trade_count == 2
    assert features.aggressor_notional == pytest.approx(1200 - 601)
    assert features.aggressor_flow_ratio == pytest.approx((1200 - 601) / (1200 + 601))
    assert features.pancake_total_amount_wei == 300_000_000_000_000_000_000
    assert features.pancake_bull_share == pytest.approx(1 / 3)
    assert features.pancake_pool_imbalance == pytest.approx(-1 / 3)
    assert features.time_to_lock_seconds == pytest.approx(20.0)
    assert features.as_dict()["pancake_log_total_amount"] == pytest.approx(math.log1p(features.pancake_total_amount_wei))


def test_future_observed_book_ticker_cannot_enter_features(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(
            normalize_book_ticker(
                {"u": 1, "s": "BNBUSDT", "b": "599", "B": "10", "a": "601", "A": "12"},
                observed_at_ns=111_000_000_000,
            )
        )
        store.append(
            normalize_latest_round_data(
                (10, 598_00000000, 99, 100, 10),
                decimals=8,
                feed_address=FEED,
                observed_at_ns=101_000_000_000,
            )
        )
        store.append(
            normalize_round_snapshot(
                _round(),
                contract_address=PREDICTION,
                treasury_fee_units=300,
                block_number=1_000,
                block_timestamp_s=104,
                observed_at_ns=105_000_000_000,
            )
        )
        events = store.read_all_ingest_order()

    snapshot = build_snapshot(events, cutoff_ns=110_000_000_000)
    with pytest.raises(ValueError, match="missing required source event: binance_spot/market.book_ticker"):
        build_core_features(snapshot)
