from __future__ import annotations

import math

import pytest

from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.portable_features import PortableFeaturePolicy, build_portable_features
from pancake_prediction_ai.replay import build_snapshot
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
FEED = "0x1111111111111111111111111111111111111111"


def _trade(trade_id: int, time_s: int, price: str, quantity: str, maker: bool):
    return normalize_rest_agg_trade(
        {
            "a": trade_id,
            "p": price,
            "q": quantity,
            "f": trade_id,
            "l": trade_id,
            "T": time_s * 1000,
            "m": maker,
        },
        symbol="BNBUSDT",
        observed_at_ns=(time_s * 1_000_000_000) + 100_000_000,
    )


def _round() -> PredictionRoundState:
    return PredictionRoundState(
        epoch=7,
        start_timestamp=50,
        lock_timestamp=120,
        close_timestamp=180,
        lock_price=0,
        close_price=0,
        lock_oracle_id=0,
        close_oracle_id=0,
        total_amount_wei=400,
        bull_amount_wei=300,
        bear_amount_wei=100,
        reward_base_cal_amount_wei=0,
        reward_amount_wei=0,
        oracle_called=False,
    )


def test_portable_features_require_no_book_ticker_and_match_public_historical_sources(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_trade(1, 80, "598", "2", False))
        store.append(_trade(2, 100, "600", "1", False))
        store.append(_trade(3, 108, "602", "1", True))
        store.append(
            normalize_latest_round_data(
                (10, 599_00000000, 90, 100, 10),
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
                block_timestamp_s=105,
                observed_at_ns=105_500_000_000,
            )
        )
        snapshot = build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)

    features = build_portable_features(
        snapshot,
        policy=PortableFeaturePolicy(long_window_ns=30_000_000_000, short_window_ns=10_000_000_000),
    )
    assert features.binance_last_trade_price == 602.0
    assert features.chainlink_price == 599.0
    assert features.binance_chainlink_divergence_bps == pytest.approx((602 - 599) / 599 * 10_000)
    assert features.oracle_age_seconds == pytest.approx(10.0)
    assert features.last_trade_age_seconds == pytest.approx(2.0)
    assert features.trade_count_long == 3
    assert features.trade_count_short == 2
    assert features.price_change_bps_long == pytest.approx((602 - 598) / 598 * 10_000)
    assert features.price_change_bps_short == pytest.approx((602 - 600) / 600 * 10_000)
    assert features.aggressor_flow_ratio_short == pytest.approx((600 - 602) / (600 + 602))
    expected_vol = math.sqrt(math.log(600 / 598) ** 2 + math.log(602 / 600) ** 2) * 10_000
    assert features.realized_volatility_bps_long == pytest.approx(expected_vol)
    assert features.pancake_bull_share == pytest.approx(0.75)
    assert features.pancake_pool_imbalance == pytest.approx(0.5)
    assert features.time_to_lock_seconds == pytest.approx(10.0)
    assert "pancake_log_total_amount" in features.as_dict()


def test_future_observed_trade_is_excluded_even_if_trade_timestamp_is_old(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        old_but_future_observed = normalize_rest_agg_trade(
            {"a": 1, "p": "600", "q": "1", "f": 1, "l": 1, "T": 100_000, "m": False},
            symbol="BNBUSDT",
            observed_at_ns=120_000_000_000,
        )
        store.append(old_but_future_observed)
        store.append(
            normalize_latest_round_data(
                (10, 599_00000000, 90, 100, 10),
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
                block_timestamp_s=105,
                observed_at_ns=105_500_000_000,
            )
        )
        snapshot = build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)

    with pytest.raises(ValueError, match="missing Binance aggregate trades"):
        build_portable_features(snapshot)
