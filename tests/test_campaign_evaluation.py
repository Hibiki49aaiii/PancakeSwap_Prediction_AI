from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction.baseline import ALL_FEATURE_NAMES, ResearchFeatureRow
from pancake_prediction.campaign_evaluation import (
    EconomicCampaignConfig,
    run_source_bound_economic_campaign,
)
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord

WEI = 10**18


def _round(epoch: int) -> RoundRecord:
    start = 1_000 + epoch * 400
    label = "bull" if epoch % 2 else "bear"
    post_bull = 8 * WEI if label == "bull" else 2 * WEI
    post_bear = 8 * WEI if label == "bear" else 2 * WEI
    bull = 10 * WEI + post_bull
    bear = 10 * WEI + post_bear
    return RoundRecord(
        epoch=epoch,
        start_block=epoch * 10,
        start_timestamp=start,
        lock_block=epoch * 10 + 1,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=60_000_000_000,
        end_block=epoch * 10 + 2,
        end_timestamp=start + 360,
        close_round_id=epoch + 1,
        close_price=60_100_000_000 if label == "bull" else 59_900_000_000,
        bull_amount_wei=bull,
        bear_amount_wei=bear,
        total_amount_wei=bull + bear,
        bet_count=4,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label=label,
        issues=(),
    )


def _bet(epoch: int, timestamp: int, amount: int, side: str, index: int) -> ChainEvent:
    return ChainEvent(
        block_number=epoch * 10 + index,
        block_hash="0x" + f"{epoch * 10 + index:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{epoch * 100 + index:064x}",
        tx_index=0,
        log_index=index,
        event_name=side,
        decoded={
            "epoch": epoch,
            "amount": amount,
            "sender": "0x" + f"{epoch * 10 + index:040x}",
        },
    )


def _events(rounds: tuple[RoundRecord, ...]) -> tuple[ChainEvent, ...]:
    events: list[ChainEvent] = []
    for record in rounds:
        assert record.start_timestamp is not None
        post_bull = record.bull_amount_wei - 10 * WEI
        post_bear = record.bear_amount_wei - 10 * WEI
        events.extend(
            (
                _bet(record.epoch, record.start_timestamp + 100, 10 * WEI, "BetBull", 1),
                _bet(record.epoch, record.start_timestamp + 110, 10 * WEI, "BetBear", 2),
                _bet(record.epoch, record.start_timestamp + 290, post_bull, "BetBull", 3),
                _bet(record.epoch, record.start_timestamp + 291, post_bear, "BetBear", 4),
            )
        )
    return tuple(events)


def _feature_row(record: RoundRecord) -> ResearchFeatureRow:
    assert record.start_timestamp is not None
    direction = 1.0 if record.label == "bull" else -1.0
    values = {name: 0.0 for name in ALL_FEATURE_NAMES}
    values.update(
        {
            "oracle_age_ms": 1_000.0,
            "spot_oracle_gap_ppm": direction * 20_000.0,
            "perp_oracle_gap_ppm": direction * 18_000.0,
            "spot_perp_basis_ppm": direction * 2_000.0,
            "spot_flow_imbalance_ppm": direction * 800_000.0,
            "perp_flow_imbalance_ppm": direction * 700_000.0,
            "pool_bull_share_ppm": 500_000.0,
            "prior_bull_rate_20_ppm": 500_000.0,
        }
    )
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=record.epoch,
        decision_timestamp_ms=(record.start_timestamp + 280) * 1_000,
        values=values,
    )


def _config() -> EconomicCampaignConfig:
    return EconomicCampaignConfig(
        stake_wei=WEI // 100,
        bet_gas_wei=10**14,
        claim_gas_wei=10**14,
        inclusion_latency_seconds=2,
        min_expected_value_wei=-(10**18),
        min_train_rounds=8,
        test_rounds=4,
        purge_rounds=1,
        embargo_rounds=0,
        calibration_rounds=3,
        pool_min_train_rounds=4,
        pool_window_rounds=12,
    )


def test_source_bound_campaign_runs_purged_probability_and_economic_layers() -> None:
    rounds = tuple(_round(epoch) for epoch in range(1, 25))
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, rounds)
    report = run_source_bound_economic_campaign(
        replay,
        _events(rounds),
        tuple(_feature_row(record) for record in rounds),
        campaign_digest="b" * 64,
        config=_config(),
    )
    payload = report.as_dict()
    assert report.campaign_digest == "b" * 64
    assert report.fold_count > 0
    assert report.direction_signal_count > 0
    assert report.pool_projection_count > 0
    assert report.joint_epoch_count > 0
    assert payload["probability_metrics"]["n_scored"] > 0
    assert payload["backtest_summary"]["trade_count"] > 0
    assert "trades" not in payload["backtest_summary"]
    assert len(payload["evaluation_digest"]) == 64


def test_evaluation_digest_binds_explicit_cost_and_latency_assumptions() -> None:
    rounds = tuple(_round(epoch) for epoch in range(1, 25))
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, rounds)
    events = _events(rounds)
    rows = tuple(_feature_row(record) for record in rounds)
    base = run_source_bound_economic_campaign(
        replay,
        events,
        rows,
        campaign_digest="b" * 64,
        config=_config(),
    )
    changed = run_source_bound_economic_campaign(
        replay,
        events,
        rows,
        campaign_digest="b" * 64,
        config=replace(_config(), bet_gas_wei=2 * 10**14),
    )
    assert base.evaluation_digest != changed.evaluation_digest


def test_source_bound_campaign_rejects_invalid_manifest_digest() -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    with pytest.raises(ValueError, match="campaign_digest"):
        run_source_bound_economic_campaign(
            replay,
            (),
            (),
            campaign_digest="not-a-digest",
            config=_config(),
        )
