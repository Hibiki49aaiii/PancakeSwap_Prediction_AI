from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction.shadow_campaign import (
    ShadowCampaignPolicy,
    evaluate_shadow_campaign,
)
from pancake_prediction.shadow_ledger import ShadowLedgerAuditReport


def _audit(**overrides: object) -> ShadowLedgerAuditReport:
    values: dict[str, object] = {
        "event_count": 1_950,
        "head_digest": "a" * 64,
        "prediction_count": 1_000,
        "settlement_count": 950,
        "unresolved_count": 50,
        "actionable_prediction_count": 500,
        "settled_actionable_count": 480,
        "probability_scored_count": 940,
        "brier_score": 0.24,
        "directional_accuracy": 0.53,
        "observed_pnl_count": 480,
        "observed_pnl_wei": -123_456,
        "first_decision_timestamp_ms": 1_000_000,
        "last_decision_timestamp_ms": 1_000_000 + 8 * 24 * 60 * 60 * 1_000,
        "markets": ("BNBUSD",),
        "model_ids": ("shadow-wf-v1",),
        "feature_set_ids": ("full-v1",),
        "action_counts": {"bull": 250, "bear": 250, "skip": 500},
        "integrity_errors": (),
    }
    values.update(overrides)
    return ShadowLedgerAuditReport(**values)  # type: ignore[arg-type]


def test_shadow_campaign_gate_can_pass_with_negative_pnl() -> None:
    report = evaluate_shadow_campaign(_audit())
    payload = report.as_dict()

    assert report.gate_ready is True
    assert report.unresolved_ppm == 50_000
    assert report.settlement_coverage_ppm == 950_000
    assert report.actionable_pnl_coverage_ppm == 1_000_000
    assert report.audit.observed_pnl_wei < 0
    assert payload["profitability_gate_eligible"] is False
    assert payload["full_historical_gate_satisfied"] is False
    assert payload["signing_enabled"] is False
    assert payload["live_broadcast"] is False
    assert len(report.campaign_digest) == 64
    assert report.campaign_digest == evaluate_shadow_campaign(_audit()).campaign_digest


def test_shadow_campaign_gate_fails_unresolved_or_short_campaign() -> None:
    unresolved = evaluate_shadow_campaign(
        _audit(unresolved_count=200, settlement_count=800)
    )
    assert unresolved.gate_ready is False
    assert unresolved.checks["unresolved_rate_within_limit"] is False
    assert unresolved.checks["settlement_count_sufficient"] is False

    short = evaluate_shadow_campaign(
        _audit(last_decision_timestamp_ms=1_000_000 + 6 * 24 * 60 * 60 * 1_000)
    )
    assert short.gate_ready is False
    assert short.checks["decision_span_sufficient"] is False


def test_shadow_campaign_gate_requires_both_directions_and_complete_pnl() -> None:
    one_direction = evaluate_shadow_campaign(
        _audit(action_counts={"bull": 500, "bear": 0, "skip": 500})
    )
    assert one_direction.gate_ready is False
    assert one_direction.checks["both_directions_observed"] is False

    missing_pnl = evaluate_shadow_campaign(_audit(observed_pnl_count=479))
    assert missing_pnl.gate_ready is False
    assert missing_pnl.checks["actionable_pnl_complete"] is False


def test_shadow_campaign_gate_detects_model_or_feature_drift() -> None:
    model_drift = evaluate_shadow_campaign(
        _audit(model_ids=("shadow-wf-v1", "shadow-wf-v2"))
    )
    assert model_drift.gate_ready is False
    assert model_drift.checks["model_version_count_within_limit"] is False

    feature_drift = evaluate_shadow_campaign(
        _audit(feature_set_ids=("full-v1", "without-round_history-v1"))
    )
    assert feature_drift.gate_ready is False
    assert feature_drift.checks["feature_set_count_within_limit"] is False

    relaxed = ShadowCampaignPolicy(max_model_ids=2, max_feature_set_ids=2)
    relaxed_report = evaluate_shadow_campaign(
        _audit(
            model_ids=("shadow-wf-v1", "shadow-wf-v2"),
            feature_set_ids=("full-v1", "without-round_history-v1"),
        ),
        relaxed,
    )
    assert relaxed_report.gate_ready is True


def test_shadow_campaign_gate_requires_integrity_and_minimum_samples() -> None:
    report = evaluate_shadow_campaign(
        _audit(
            integrity_errors=("sequence 2: event digest mismatch",),
            prediction_count=999,
            probability_scored_count=899,
            actionable_prediction_count=199,
        )
    )
    assert report.gate_ready is False
    assert report.checks["ledger_integrity_ready"] is False
    assert report.checks["prediction_count_sufficient"] is False
    assert report.checks["probability_scored_sufficient"] is False
    assert report.checks["actionable_prediction_count_sufficient"] is False


def test_shadow_campaign_policy_validation_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="min_predictions"):
        evaluate_shadow_campaign(_audit(), ShadowCampaignPolicy(min_predictions=-1))
    with pytest.raises(ValueError, match="max_unresolved_ppm"):
        evaluate_shadow_campaign(
            _audit(),
            ShadowCampaignPolicy(max_unresolved_ppm=1_000_001),
        )
    with pytest.raises(ValueError, match="max_model_ids"):
        evaluate_shadow_campaign(_audit(), ShadowCampaignPolicy(max_model_ids=0))
    with pytest.raises(ValueError, match="max_feature_set_ids"):
        evaluate_shadow_campaign(_audit(), ShadowCampaignPolicy(max_feature_set_ids=0))


def test_shadow_campaign_custom_policy_can_define_small_smoke_gate() -> None:
    small = replace(
        ShadowCampaignPolicy(),
        min_predictions=10,
        min_settlements=8,
        min_probability_scored=8,
        min_actionable_predictions=2,
        min_decision_span_ms=60_000,
    )
    report = evaluate_shadow_campaign(
        _audit(
            prediction_count=10,
            settlement_count=9,
            unresolved_count=1,
            probability_scored_count=9,
            actionable_prediction_count=4,
            settled_actionable_count=4,
            observed_pnl_count=4,
            first_decision_timestamp_ms=1_000_000,
            last_decision_timestamp_ms=1_060_000,
            action_counts={"bull": 2, "bear": 2, "skip": 6},
        ),
        small,
    )
    assert report.gate_ready is True
