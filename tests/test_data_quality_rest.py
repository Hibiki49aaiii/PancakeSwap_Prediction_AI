from __future__ import annotations

from dataclasses import replace

from pancake_prediction_ai.data_quality import SourceQualityPolicy, assess_source_quality
from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.features import build_core_features
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.replay import build_snapshot
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade, normalize_rest_book_ticker
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
FEED = "0x1111111111111111111111111111111111111111"


def test_rest_book_sequence_unavailability_is_visible_to_quality_policy(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(
            normalize_rest_book_ticker(
                {"symbol": "BNBUSDT", "bidPrice": "599", "bidQty": "1", "askPrice": "601", "askQty": "1"},
                observed_at_ns=105_000_000_000,
            )
        )
        for trade_id, trade_time_s in ((1, 100), (2, 105)):
            store.append(
                normalize_rest_agg_trade(
                    {"a": trade_id, "p": "600", "q": "1", "f": trade_id, "l": trade_id, "T": trade_time_s * 1000, "m": False},
                    symbol="BNBUSDT",
                    observed_at_ns=106_000_000_000,
                )
            )
        store.append(
            normalize_latest_round_data(
                (10, 600_00000000, 99, 100, 10),
                decimals=8,
                feed_address=FEED,
                observed_at_ns=101_000_000_000,
            )
        )
        store.append(
            normalize_round_snapshot(
                PredictionRoundState(
                    epoch=9,
                    start_timestamp=70,
                    lock_timestamp=130,
                    close_timestamp=190,
                    lock_price=0,
                    close_price=0,
                    lock_oracle_id=0,
                    close_oracle_id=0,
                    total_amount_wei=300,
                    bull_amount_wei=120,
                    bear_amount_wei=180,
                    reward_base_cal_amount_wei=0,
                    reward_amount_wei=0,
                    oracle_called=False,
                ),
                contract_address=PREDICTION,
                treasury_fee_units=300,
                block_number=1000,
                block_timestamp_s=104,
                observed_at_ns=105_000_000_000,
            )
        )
        snapshot = build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)

    features = build_core_features(snapshot, trade_window_ns=15_000_000_000)
    strict = SourceQualityPolicy(
        max_book_observation_age_ns=10_000_000_000,
        max_oracle_source_age_ns=20_000_000_000,
        max_round_observation_age_ns=10_000_000_000,
        max_spread_bps=50,
        min_recent_trade_count=2,
        min_time_to_lock_seconds=10,
    )
    strict_report = assess_source_quality(snapshot, features, policy=strict)
    assert "binance_book_sequence_unavailable" in strict_report.blockers

    diagnostic = replace(strict, require_book_sequence_id=False)
    diagnostic_report = assess_source_quality(snapshot, features, policy=diagnostic)
    assert diagnostic_report.ok
