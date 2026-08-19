from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction_ai.evidence_gate import Evidence, EvidenceOrigin
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.shadow_evidence_artifact import (
    build_shadow_economic_evidence_artifact,
)
from pancake_prediction_ai.shadow_gate_evidence import (
    ShadowGateAcceptancePolicy,
    build_shadow_gate_evidence,
    write_shadow_gate_evidence,
)


def _artifact(tmp_path, *, claim_gas_modeled: bool = True):
    path = tmp_path / "observed.sqlite"
    store = EventStore(path)
    round_snapshot = store.append(
        EventRecord(
            event_id="round:7",
            source="pancake_prediction",
            topic="prediction.round_snapshot",
            event_time_ns=10,
            observed_at_ns=10,
            payload={"epoch": 7},
        )
    )
    model = store.append(
        EventRecord(
            event_id="shadow:model_decision:7",
            source="shadow",
            topic="shadow.model_decision",
            event_time_ns=20,
            observed_at_ns=20,
            payload={"round_id": 7},
        )
    )
    economic = store.append(
        EventRecord(
            event_id="shadow:economic_decision:7",
            source="shadow",
            topic="shadow.economic_decision",
            event_time_ns=30,
            observed_at_ns=30,
            payload={
                "round_id": 7,
                "action": "BET",
                "selected_side": "BULL",
                "stake_wei": 100,
                "model_decision_event_id": model.event.event_id,
                "promoted_model_artifact_sha256": "a" * 64,
                "source_snapshot_tip_hash": round_snapshot.event_hash,
                "source_round_snapshot_event_id": round_snapshot.event.event_id,
                "assumed_execution": {
                    "gas_cost_wei": 2,
                    "claim_or_refund_gas_cost_wei": 3 if claim_gas_modeled else None,
                    "same_side_inflow_wei": 0,
                    "opposite_side_inflow_wei": 0,
                    "execution_success_probability": 1.0,
                    "min_expected_return": 0.01,
                },
                "bull_ev": {"expected_return_on_stake": 0.2},
                "bear_ev": {"expected_return_on_stake": -0.2},
            },
        )
    )
    source = store.append(
        EventRecord(
            event_id="settlement-source:7",
            source="pancake_prediction",
            topic="prediction.settlement_snapshot",
            event_time_ns=40,
            observed_at_ns=40,
            payload={"epoch": 7},
        )
    )
    store.append(
        EventRecord(
            event_id="shadow:economic_settlement:7",
            source="shadow",
            topic="shadow.economic_settlement",
            event_time_ns=40,
            observed_at_ns=40,
            payload={
                "round_id": 7,
                "resolution": "BULL",
                "action": "BET",
                "selected_side": "BULL",
                "pnl_if_executed_wei": 50,
                "probability_adjusted_pnl_wei": 50.0,
                "economic_decision_event_id": economic.event.event_id,
                "settlement_snapshot_event_id": source.event.event_id,
                "block_number": 100,
                "block_hash": "0x" + "ab" * 32,
                "claim_or_refund_gas_modeled": claim_gas_modeled,
            },
        )
    )
    artifact = build_shadow_economic_evidence_artifact(store, generated_at_ns=1000)
    store.close()
    return artifact


def _policy() -> ShadowGateAcceptancePolicy:
    return ShadowGateAcceptancePolicy(
        min_settled_rounds=1,
        min_conditional_net_pnl_wei=1,
        max_conditional_drawdown_wei=100,
        min_average_selected_expected_return=0.01,
    )


def test_qualified_shadow_gate_evidence_is_hybrid_and_passes(tmp_path) -> None:
    artifact = _artifact(tmp_path)
    evidence = build_shadow_gate_evidence(
        artifact,
        policy=_policy(),
        recorded_at="2026-08-19T12:00:00+00:00",
    )
    assert evidence.origin is EvidenceOrigin.HYBRID
    assert evidence.passed is True
    assert evidence.payload["blockers"] == []
    assert evidence.payload["funded_live_profitability_evidence"] is False
    assert evidence.payload["stage6b_funded_validation_evidence"] is False


def test_shadow_gate_policy_cannot_disable_settlement_completeness() -> None:
    with pytest.raises(ValueError, match="all decisions settled"):
        replace(_policy(), require_all_decisions_settled=False).validate()


def test_shadow_gate_policy_cannot_disable_claim_gas_coverage() -> None:
    with pytest.raises(ValueError, match="fully costed"):
        replace(_policy(), require_fully_costed_claim_or_refund_gas=False).validate()


def test_incomplete_claim_gas_produces_failed_gate_evidence(tmp_path) -> None:
    artifact = _artifact(tmp_path, claim_gas_modeled=False)
    evidence = build_shadow_gate_evidence(
        artifact,
        policy=_policy(),
        recorded_at="2026-08-19T12:00:00+00:00",
    )
    assert evidence.passed is False
    assert "shadow_artifact_structurally_incomplete" in evidence.payload["blockers"]
    assert "shadow_claim_or_refund_gas_incomplete" in evidence.payload["blockers"]


def test_shadow_gate_evidence_writer_round_trips_through_generic_evidence_loader(tmp_path) -> None:
    evidence = build_shadow_gate_evidence(
        _artifact(tmp_path),
        policy=_policy(),
        recorded_at="2026-08-19T12:00:00+00:00",
    )
    path = tmp_path / "shadow-gate.json"
    write_shadow_gate_evidence(evidence, path)
    loaded = Evidence.from_path(path)
    assert loaded == evidence
