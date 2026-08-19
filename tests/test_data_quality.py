from __future__ import annotations

from pancake_prediction_ai.data_quality import SourceQualityPolicy, assess_source_quality
from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.features import build_core_features
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.replay import build_snapshot
from pancake_prediction_ai.sources.binance import normalize_agg_trade, normalize_book_ticker
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
FEED = "0x1111111111111111111111111111111111111111"


def _round(lock_timestamp: int = 130) -> PredictionRoundState:
    return PredictionRoundState(
        epoch=9,
        start_timestamp=70,
        lock_timestamp=lock_timestamp,
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
    )


def _trade(trade_id: int, time_s: int):
    return normalize_agg_trade(
        {
            "e": "aggTrade",
            "E": time_s * 1000,
            "s": "BNBUSDT",
            "a": trade_id,
            "p": "600",
            "q": "1",
            "f": trade_id,
            "l": trade_id,
            "T": time_s * 1000,
            "m": False,
        },
        observed_at_ns=(time_s * 1_000_000_000) + 1,
    )


def _policy() -> SourceQualityPolicy:
    return SourceQualityPolicy(
        max_book_observation_age_ns=10_000_000_000,
        max_oracle_source_age_ns=20_000_000_000,
        max_round_observation_age_ns=10_000_000_000,
        max_spread_bps=50.0,
        min_recent_trade_count=2,
        min_time_to_lock_seconds=10.0,
    )


def _base_events(store: EventStore, *, book_observed_s: int = 105, lock_timestamp: int = 130) -> None:
    store.append(
        normalize_book_ticker(
            {"u": 1, "s": "BNBUSDT", "b": "599", "B": "1", "a": "601", "A": "1"},
            observed_at_ns=book_observed_s * 1_000_000_000,
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
            _round(lock_timestamp),
            contract_address=PREDICTION,
            treasury_fee_units=300,
            block_number=1_000,
            block_timestamp_s=104,
            observed_at_ns=105_000_000_000,
        )
    )
    store.append(_trade(1, 100))
    store.append(_trade(2, 105))


def test_healthy_snapshot_clears_data_quality_gate(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        _base_events(store)
        snapshot = build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)
    features = build_core_features(snapshot, trade_window_ns=15_000_000_000)
    report = assess_source_quality(snapshot, features, policy=_policy())
    assert report.ok
    assert report.blockers == ()


def test_stale_book_insufficient_trades_and_lock_margin_are_explicit_blockers(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        _base_events(store, book_observed_s=90, lock_timestamp=115)
        snapshot = build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)
    features = build_core_features(snapshot, trade_window_ns=4_000_000_000)
    report = assess_source_quality(snapshot, features, policy=_policy())
    assert not report.ok
    assert "binance_book_stale" in report.blockers
    assert "insufficient_recent_trades" in report.blockers
    assert "decision_too_close_to_lock" in report.blockers


def test_non_monotonic_book_update_sequence_is_blocked(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        _base_events(store)
        store.append(
            normalize_book_ticker(
                {"u": 0, "s": "BNBUSDT", "b": "599", "B": "1", "a": "601", "A": "1"},
                observed_at_ns=106_000_000_000,
            )
        )
        snapshot = build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)
    features = build_core_features(snapshot, trade_window_ns=15_000_000_000)
    report = assess_source_quality(snapshot, features, policy=_policy())
    assert not report.ok
    assert "binance_book_sequence_non_monotonic" in report.blockers
