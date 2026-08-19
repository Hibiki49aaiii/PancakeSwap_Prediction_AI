from __future__ import annotations

import hashlib
import json

import pytest

from pancake_prediction_ai.evidence_gate import (
    Evidence,
    EvidenceKind,
    EvidenceOrigin,
    RuntimeSafetyState,
    evaluate_stage6a_readiness,
)


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def evidence(
    kind: EvidenceKind,
    *,
    origin: EvidenceOrigin = EvidenceOrigin.OBSERVED,
    passed: bool = True,
) -> Evidence:
    payload: dict[str, object] = {"kind": kind.value, "test": "fixture", "passed": passed}
    return Evidence(
        kind,
        origin,
        passed,
        _digest(payload),
        "2026-08-19T00:00:00+09:00",
        payload,
    )


def qualified_shadow(
    *,
    passed: bool = True,
    origin: EvidenceOrigin = EvidenceOrigin.HYBRID,
    blockers: list[str] | None = None,
    settled_rounds: int = 20,
    unresolved_rounds: int = 0,
    claim_gas_modeled: bool = True,
) -> Evidence:
    blocker_values = [] if blockers is None else blockers
    payload: dict[str, object] = {
        "schema": "shadow_gate_evidence_v1",
        "shadow_evidence_artifact_sha256": "a" * 64,
        "shadow_artifact_schema": "shadow_economic_evidence_v1",
        "artifact_class": "hybrid_shadow_not_live",
        "source_event_store_availability": "observed",
        "source_event_store_tip_hash": "b" * 64,
        "acceptance_policy": {
            "min_settled_rounds": 10,
            "min_conditional_net_pnl_wei": 1,
            "max_conditional_drawdown_wei": 100,
            "min_average_selected_expected_return": 0.01,
            "require_all_decisions_settled": True,
            "require_fully_costed_claim_or_refund_gas": True,
        },
        "metrics": {
            "settled_rounds": settled_rounds,
            "unresolved_rounds": unresolved_rounds,
            "conditional_net_pnl_wei": 50,
            "conditional_max_drawdown_wei": 20,
            "average_selected_expected_return": 0.05,
            "claim_or_refund_gas_fully_modeled": claim_gas_modeled,
        },
        "blockers": blocker_values,
        "funded_live_profitability_evidence": False,
        "stage6b_funded_validation_evidence": False,
    }
    return Evidence(
        EvidenceKind.SHADOW_ECONOMICS,
        origin,
        passed,
        _digest(payload),
        "2026-08-19T00:00:00+09:00",
        payload,
    )


def safety(**changes: object) -> RuntimeSafetyState:
    values = dict(
        kill_switch_armed=True,
        wallet_binding_ok=True,
        per_round_cap_ok=True,
        balance_cap_ok=True,
        unresolved_intents=0,
        decision_window_open=True,
        signing_enabled=False,
        mainnet_broadcast_enabled=False,
    )
    values.update(changes)
    return RuntimeSafetyState(**values)  # type: ignore[arg-type]


def test_observed_stage5_evidence_plus_qualified_hybrid_shadow_can_clear_stage6a() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert decision.ready
    assert decision.blockers == ()


@pytest.mark.parametrize("origin", [EvidenceOrigin.ASSUMED, EvidenceOrigin.SELF_REPORTED])
def test_non_observed_stage5b_never_clears_gate(origin: EvidenceOrigin) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK, origin=origin),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_observed_pass_missing" in decision.blockers


def test_generic_observed_shadow_json_cannot_clear_gate_anymore() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=evidence(EvidenceKind.SHADOW_ECONOMICS),
        safety=safety(),
    )
    assert not decision.ready
    assert "shadow_qualified_hybrid_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    "shadow",
    [
        qualified_shadow(origin=EvidenceOrigin.OBSERVED),
        qualified_shadow(passed=False),
        qualified_shadow(blockers=["policy_failed"]),
        qualified_shadow(settled_rounds=9),
        qualified_shadow(unresolved_rounds=1),
        qualified_shadow(claim_gas_modeled=False),
    ],
)
def test_incomplete_or_misclassified_shadow_evidence_cannot_clear_gate(shadow: Evidence) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=shadow,
        safety=safety(),
    )
    assert not decision.ready
    assert "shadow_qualified_hybrid_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    ("changes", "blocker"),
    [
        ({"kill_switch_armed": False}, "kill_switch_not_armed"),
        ({"wallet_binding_ok": False}, "wallet_binding_failed"),
        ({"per_round_cap_ok": False}, "per_round_cap_failed"),
        ({"balance_cap_ok": False}, "balance_cap_failed"),
        ({"unresolved_intents": 1}, "unresolved_intents_present"),
        ({"decision_window_open": False}, "decision_window_closed"),
        ({"signing_enabled": True}, "signing_enabled_during_preflight"),
        ({"mainnet_broadcast_enabled": True}, "mainnet_broadcast_enabled_during_preflight"),
    ],
)
def test_each_runtime_safety_failure_blocks(changes: dict[str, object], blocker: str) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=qualified_shadow(),
        safety=safety(**changes),
    )
    assert not decision.ready
    assert blocker in decision.blockers


def test_evidence_json_requires_payload_hash_match() -> None:
    raw = json.dumps(
        {
            "kind": "stage5b_fork",
            "origin": "observed",
            "passed": True,
            "recorded_at": "2026-08-19T00:00:00+09:00",
            "artifact_sha256": "0" * 64,
            "payload": {"fork_executed": True},
        }
    ).encode()
    with pytest.raises(ValueError, match="artifact_sha256"):
        Evidence.from_json_bytes(raw)