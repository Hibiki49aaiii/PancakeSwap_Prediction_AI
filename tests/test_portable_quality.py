from __future__ import annotations

from dataclasses import replace

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.portable_features import PortableFeaturePolicy, build_portable_features
from pancake_prediction_ai.portable_quality import PortableQualityPolicy, assess_portable_quality
from pancake_prediction_ai.replay import build_snapshot
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
FEED = "0x1111111111111111111111111111111111111111"


def _round_event(observed_at_ns: int, *, block_number: int = 100):
    return normalize_round_snapshot(
        PredictionRoundState(
            epoch=7,
            start_timestamp=50,
            lock_timestamp=120,
            close_timestamp=180,
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
        block_number=block_number,
        block_timestamp_s=105,
        observed_at_ns=observed_at_ns,
    )


def _anchor(observed_at_ns: int, *, block_number: int = 100):
    return EventRecord(
        event_id=f"anchor:{block_number}:{observed_at_ns}",
        source="collector",
        topic="collector.protocol_block_anchor",
        event_time_ns=105_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "block_number": block_number,
            "block_hash": "0x" + "ab" * 32,
            "parent_hash": "0x" + "cd" * 32,
            "block_timestamp_s": 105,
        },
    )


def _trade(trade_id: int, time_s: int):
    return normalize_rest_agg_trade(
        {"a": trade_id, "p": "600", "q": "1", "f": trade_id, "l": trade_id, "T": time_s * 1000, "m": False},
        symbol="BNBUSDT",
        observed_at_ns=time_s * 1_000_000_000 + 100_000_000,
    )


def _snapshot(tmp_path, *, include_anchor=True, anomaly=False, anchor_block=100):
    with EventStore(tmp_path / "observed.sqlite") as store:
        for trade_id, time_s in enumerate((90, 95, 100, 105, 108), start=1):
            store.append(_trade(trade_id, time_s))
        store.append(
            normalize_latest_round_data(
                (10, 599_00000000, 90, 100, 10),
                decimals=8,
                feed_address=FEED,
                observed_at_ns=101_000_000_000,
            )
        )
        if include_anchor:
            store.append(_anchor(105_000_000_000, block_number=anchor_block))
        store.append(_round_event(105_000_000_000))
        if anomaly:
            store.append(
                EventRecord(
                    event_id="anomaly",
                    source="collector",
                    topic="collector.protocol_anomaly",
                    event_time_ns=106_000_000_000,
                    observed_at_ns=106_000_000_000,
                    payload={"anomaly": "parent_hash_mismatch"},
                )
            )
        return build_snapshot(store.read_all_ingest_order(), cutoff_ns=110_000_000_000)


def test_healthy_portable_observation_clears_strict_gate(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)
    features = build_portable_features(
        snapshot,
        policy=PortableFeaturePolicy(long_window_ns=30_000_000_000, short_window_ns=10_000_000_000),
    )
    report = assess_portable_quality(
        snapshot,
        features,
        policy=PortableQualityPolicy(
            max_oracle_age_seconds=20,
            max_last_trade_age_seconds=5,
            max_round_observation_age_ns=10_000_000_000,
            min_trade_count_long=5,
            min_trade_count_short=2,
            min_time_to_lock_seconds=5,
            max_time_to_lock_seconds=20,
        ),
    )
    assert report.ok
    assert report.blockers == ()
    assert report.latest_protocol_block_number == 100
    assert report.latest_round_block_number == 100


def test_missing_or_mismatched_anchor_blocks_live_decision(tmp_path) -> None:
    missing = _snapshot(tmp_path / "missing", include_anchor=False)
    missing_features = build_portable_features(missing)
    assert "protocol_block_anchor_missing" in assess_portable_quality(missing, missing_features).blockers

    mismatch = _snapshot(tmp_path / "mismatch", anchor_block=99)
    mismatch_features = build_portable_features(mismatch)
    assert "protocol_anchor_round_block_mismatch" in assess_portable_quality(mismatch, mismatch_features).blockers


def test_reorg_anomaly_observed_after_round_snapshot_blocks_decision(tmp_path) -> None:
    snapshot = _snapshot(tmp_path, anomaly=True)
    features = build_portable_features(snapshot)
    report = assess_portable_quality(snapshot, features)
    assert not report.ok
    assert "protocol_anomaly_since_round_snapshot" in report.blockers


def test_stale_and_timing_conditions_are_independent_blockers(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)
    features = build_portable_features(snapshot)
    stale = replace(
        features,
        oracle_age_seconds=100.0,
        last_trade_age_seconds=100.0,
        trade_count_short=0,
        time_to_lock_seconds=1.0,
    )
    report = assess_portable_quality(snapshot, stale)
    assert "oracle_stale" in report.blockers
    assert "last_trade_stale" in report.blockers
    assert "insufficient_short_window_trades" in report.blockers
    assert "decision_too_close_to_lock" in report.blockers
