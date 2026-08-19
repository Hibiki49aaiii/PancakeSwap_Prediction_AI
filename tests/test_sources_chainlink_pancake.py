from __future__ import annotations

import pytest

from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_oracle_reference, normalize_round_snapshot


FEED = "0x1111111111111111111111111111111111111111"
PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"


def test_chainlink_latest_round_keeps_updated_at_and_observation_time_separate() -> None:
    event = normalize_latest_round_data(
        (123, 600_12345678, 1_700_000_000, 1_700_000_010, 123),
        decimals=8,
        feed_address=FEED,
        observed_at_ns=1_700_000_011_500_000_000,
        description="BNB / USD",
    )
    assert event.source == "chainlink"
    assert event.topic == "oracle.latest_round"
    assert event.event_time_ns == 1_700_000_010_000_000_000
    assert event.observed_at_ns == 1_700_000_011_500_000_000
    assert event.payload["price"] == pytest.approx(600.12345678)
    assert event.payload["round_id"] == 123


def test_chainlink_stale_round_relation_is_rejected() -> None:
    with pytest.raises(ValueError, match="answeredInRound"):
        normalize_latest_round_data(
            (123, 600_00000000, 10, 11, 122),
            decimals=8,
            feed_address=FEED,
            observed_at_ns=12_000_000_000,
        )


def _round() -> PredictionRoundState:
    return PredictionRoundState(
        epoch=99,
        start_timestamp=1000,
        lock_timestamp=1300,
        close_timestamp=1600,
        lock_price=600_00000000,
        close_price=0,
        lock_oracle_id=123,
        close_oracle_id=0,
        total_amount_wei=300,
        bull_amount_wei=120,
        bear_amount_wei=180,
        reward_base_cal_amount_wei=0,
        reward_amount_wei=0,
        oracle_called=False,
    )


def test_pancake_round_snapshot_is_block_anchored() -> None:
    event = normalize_round_snapshot(
        _round(),
        contract_address=PREDICTION,
        treasury_fee_units=300,
        block_number=50_000_000,
        block_timestamp_s=1_700_000_000,
        observed_at_ns=1_700_000_001_000_000_000,
    )
    assert event.event_time_ns == 1_700_000_000_000_000_000
    assert event.observed_at_ns == 1_700_000_001_000_000_000
    assert event.payload["treasury_fee_units"] == 300
    assert event.payload["treasury_fee_ppm"] == 30_000
    assert event.payload["bull_amount_wei"] == 120
    assert event.payload["bear_amount_wei"] == 180


def test_oracle_reference_is_discovered_from_prediction_contract_state() -> None:
    event = normalize_oracle_reference(
        FEED,
        contract_address=PREDICTION,
        block_number=50_000_000,
        block_timestamp_s=1_700_000_000,
        observed_at_ns=1_700_000_001_000_000_000,
    )
    assert event.topic == "prediction.oracle_reference"
    assert event.payload["oracle_address"] == FEED.lower()
    assert event.payload["contract_address"] == PREDICTION.lower()
