from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction.baseline import ALL_FEATURE_NAMES, ResearchFeatureRow
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord
from pancake_prediction.shadow_inference import (
    ShadowInferenceConfig,
    build_shadow_inference,
)


def _round(
    epoch: int,
    *,
    label: str,
    bull_final: int = 160,
    bear_final: int = 140,
) -> RoundRecord:
    start = epoch * 1_000
    return RoundRecord(
        epoch=epoch,
        start_block=epoch * 10,
        start_timestamp=start,
        lock_block=epoch * 10 + 1,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=100,
        end_block=epoch * 10 + 2,
        end_timestamp=start + 600,
        close_round_id=epoch + 1,
        close_price=101 if label == "bull" else 99,
        bull_amount_wei=bull_final,
        bear_amount_wei=bear_final,
        total_amount_wei=bull_final + bear_final,
        bet_count=4,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label=label,
        issues=(),
    )


def _bet(epoch: int, *, timestamp: int, amount: int, side: str, index: int) -> ChainEvent:
    return ChainEvent(
        block_number=epoch * 10,
        block_hash="0x" + f"{epoch * 10:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{epoch * 100 + index:064x}",
        tx_index=0,
        log_index=index,
        event_name=side,
        decoded={
            "epoch": epoch,
            "amount": amount,
            "sender": "0x" + f"{index + 1:040x}",
        },
    )


def _row(epoch: int, *, signal: float) -> ResearchFeatureRow:
    values = {name: 0.0 for name in ALL_FEATURE_NAMES}
    values["spot_oracle_gap_ppm"] = signal
    values["spot_flow_imbalance_ppm"] = signal * 0.7
    values["perp_flow_imbalance_ppm"] = signal * 0.4
    values["pool_bull_share_ppm"] = 500_000.0 + signal / 10
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=epoch,
        decision_timestamp_ms=(epoch * 1_000 + 280) * 1_000,
        values=values,
    )


def _fixture(
    *,
    target_epoch: int = 40,
) -> tuple[ReplaySnapshot, tuple[ChainEvent, ...], tuple[ResearchFeatureRow, ...]]:
    rounds = tuple(
        _round(
            epoch,
            label="bull" if epoch % 2 else "bear",
        )
        for epoch in range(1, target_epoch + 1)
    )
    events: list[ChainEvent] = []
    rows: list[ResearchFeatureRow] = []
    for epoch in range(1, target_epoch + 1):
        start = epoch * 1_000
        events.extend(
            (
                _bet(
                    epoch,
                    timestamp=start + 100,
                    amount=100,
                    side="BetBull",
                    index=0,
                ),
                _bet(
                    epoch,
                    timestamp=start + 120,
                    amount=100,
                    side="BetBear",
                    index=1,
                ),
            )
        )
        rows.append(
            _row(
                epoch,
                signal=120_000.0 if epoch % 2 else -120_000.0,
            )
        )
    return (
        ReplaySnapshot(1, "BNBUSD", "a" * 64, rounds),
        tuple(events),
        tuple(rows),
    )


def _config() -> ShadowInferenceConfig:
    return ShadowInferenceConfig(
        min_train_rounds=20,
        calibration_rounds=5,
        calibration_bins=5,
        calibration_shrinkage=1,
        purge_rounds=2,
        pool_min_train_rounds=10,
        pool_window_rounds=20,
        stake_wei=10,
        bet_gas_wei=0,
        claim_gas_wei=0,
        inclusion_latency_seconds=2,
        min_expected_value_wei=0,
        decision_lead_seconds=20,
    )


def test_shadow_inference_is_purged_calibrated_and_pool_projected() -> None:
    replay, events, rows = _fixture()
    result = build_shadow_inference(
        replay,
        events,
        rows,
        target_epoch=40,
        config=_config(),
    )

    prediction = result.prediction
    assert prediction.market == "BNBUSD"
    assert prediction.epoch == 40
    assert prediction.decision_timestamp_ms == 40_280_000
    assert prediction.train_max_epoch <= 37
    assert prediction.action in {"bull", "bear", "skip"}
    assert 0 <= prediction.raw_probability_ppm <= 1_000_000
    assert 0 <= prediction.calibrated_probability_ppm <= 1_000_000
    assert result.training_row_count >= 20
    assert result.fit_row_count == result.training_row_count - 5
    assert result.calibration_row_count == 5
    assert result.projection.epoch == 40
    assert result.projection.generated_at == 40_280
    assert result.projection.train_max_epoch is not None
    assert result.projection.train_max_epoch <= 37
    assert result.projection.projected_bull_wei >= 100
    assert result.projection.projected_bear_wei >= 100
    assert prediction.metadata is not None
    assert prediction.metadata["projection_model_id"] == result.projection.model_id
    assert result.as_dict()["signing_enabled"] is False
    assert result.as_dict()["live_broadcast"] is False


def test_target_final_outcome_and_final_pool_cannot_change_shadow_decision() -> None:
    replay, events, rows = _fixture()
    first = build_shadow_inference(
        replay,
        events,
        rows,
        target_epoch=40,
        config=_config(),
    )

    changed_target = replace(
        replay.rounds[-1],
        label="bull" if replay.rounds[-1].label != "bull" else "bear",
        close_price=50_000,
        bull_amount_wei=99_999,
        bear_amount_wei=1,
        total_amount_wei=100_000,
    )
    changed_replay = replace(
        replay,
        rounds=(*replay.rounds[:-1], changed_target),
    )
    second = build_shadow_inference(
        changed_replay,
        events,
        rows,
        target_epoch=40,
        config=_config(),
    )

    assert second.prediction == first.prediction
    assert second.snapshot == first.snapshot
    assert second.projection == first.projection
    assert second.bull_expected_value_wei == first.bull_expected_value_wei
    assert second.bear_expected_value_wei == first.bear_expected_value_wei


def test_shadow_inference_rejects_target_feature_timestamp_mismatch() -> None:
    replay, events, rows = _fixture()
    changed_rows = (
        *rows[:-1],
        replace(rows[-1], decision_timestamp_ms=rows[-1].decision_timestamp_ms + 1),
    )
    with pytest.raises(ValueError, match="decision timestamp does not match"):
        build_shadow_inference(
            replay,
            events,
            changed_rows,
            target_epoch=40,
            config=_config(),
        )


def test_shadow_inference_rejects_insufficient_settled_training_rows() -> None:
    replay, events, rows = _fixture(target_epoch=15)
    with pytest.raises(ValueError, match="insufficient settled training rows"):
        build_shadow_inference(
            replay,
            events,
            rows,
            target_epoch=15,
            config=_config(),
        )


def test_shadow_inference_config_rejects_timing_without_inclusion_margin() -> None:
    with pytest.raises(ValueError, match="less than decision_lead_seconds"):
        replace(
            _config(),
            inclusion_latency_seconds=20,
            decision_lead_seconds=20,
        ).validate()
