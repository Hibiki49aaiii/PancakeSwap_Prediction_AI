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
from pancake_prediction_ai.runtime_fingerprint import (
    capture_runtime_fingerprint,
    fingerprint_sha256,
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


def qualified_stage5a(
    *,
    origin: EvidenceOrigin = EvidenceOrigin.OBSERVED,
    passed: bool = True,
    changes: dict[str, object] | None = None,
) -> Evidence:
    runtime = capture_runtime_fingerprint()
    runtime_payload = runtime.payload()
    payload: dict[str, object] = {
        "schema": "stage5a_execution_drill_v2",
        "drill_type": "local_sqlite_execution_state_durability",
        "blockchain_transaction_created": False,
        "transaction_signed": False,
        "transaction_broadcast": False,
        "runtime_fingerprint": runtime_payload,
        "runtime_fingerprint_sha256": runtime.sha256,
        "journal_mode_wal": True,
        "synchronous_full": True,
        "unresolved_recovered_after_restart": True,
        "duplicate_active_nonce_rejected": True,
        "unknown_state_persisted_after_missing_receipt": True,
        "finalized_state_persisted_after_confirmations": True,
        "terminal_nonce_released": True,
        "terminal_reuse_cleanup_persisted": True,
        "unresolved_count_final": 0,
        "required_confirmations": 3,
    }
    if changes:
        payload.update(changes)
    return Evidence(
        EvidenceKind.STAGE5A_DRILL,
        origin,
        passed,
        _digest(payload),
        "2026-08-19T00:00:00+09:00",
        payload,
    )


def qualified_stage5b(
    *,
    origin: EvidenceOrigin = EvidenceOrigin.OBSERVED,
    passed: bool = True,
    changes: dict[str, object] | None = None,
) -> Evidence:
    block_hash = "0x" + "ab" * 32
    payload: dict[str, object] = {
        "schema": "stage5b_verified_local_bsc_fork_v1",
        "probe_type": "verified_local_bsc_fork",
        "transaction_signed": False,
        "mainnet_transaction_broadcast": False,
        "chain_id": 56,
        "initial_block": 123,
        "mined_block": 124,
        "reset_block": 123,
        "prediction_contract_code_present": True,
        "chainlink_contract_code_present": True,
        "prediction_code_present_after_reset": True,
        "chainlink_code_present_after_reset": True,
        "fork_reset_supported": True,
        "fork_mine_observed": True,
        "upstream_chain_id": 56,
        "local_initial_block_hash": block_hash,
        "upstream_fork_block_hash": block_hash,
        "local_reset_block_hash": block_hash,
        "fork_block_hash_matches_upstream": True,
        "reset_block_hash_matches_upstream": True,
        "prediction_code_matches_upstream": True,
        "chainlink_code_matches_upstream": True,
        "prediction_code_matches_upstream_after_reset": True,
        "chainlink_code_matches_upstream_after_reset": True,
        "upstream_verified": True,
    }
    if changes:
        payload.update(changes)
    return Evidence(
        EvidenceKind.STAGE5B_FORK,
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
    require_all_decisions_settled: bool = True,
    require_fully_costed_claim_or_refund_gas: bool = True,
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
            "require_all_decisions_settled": require_all_decisions_settled,
            "require_fully_costed_claim_or_refund_gas": (
                require_fully_costed_claim_or_refund_gas
            ),
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


def test_qualified_stage5_evidence_plus_qualified_hybrid_shadow_can_clear_stage6a() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert decision.ready
    assert decision.blockers == ()


def test_generic_observed_stage5_json_cannot_clear_gate_anymore() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5a_qualified_observed_pass_missing" in decision.blockers
    assert "stage5b_qualified_observed_pass_missing" in decision.blockers


@pytest.mark.parametrize("origin", [EvidenceOrigin.ASSUMED, EvidenceOrigin.SELF_REPORTED])
def test_non_observed_stage5b_never_clears_gate(origin: EvidenceOrigin) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(origin=origin),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_qualified_observed_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    "changes",
    [
        {"journal_mode_wal": False},
        {"synchronous_full": False},
        {"duplicate_active_nonce_rejected": False},
        {"unknown_state_persisted_after_missing_receipt": False},
        {"finalized_state_persisted_after_confirmations": False},
        {"terminal_nonce_released": False},
        {"unresolved_count_final": 1},
        {"required_confirmations": 0},
        {"transaction_signed": True},
        {"transaction_broadcast": True},
    ],
)
def test_incomplete_or_unsafe_stage5a_payload_cannot_clear_gate(changes: dict[str, object]) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(changes=changes),
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5a_qualified_observed_pass_missing" in decision.blockers


def test_stage5a_evidence_from_different_runtime_stack_cannot_clear_gate() -> None:
    current = capture_runtime_fingerprint().payload()
    other = dict(current)
    other["python_version"] = "0.0.0-different-runtime"
    other_sha = fingerprint_sha256(other)
    stage5a = qualified_stage5a(
        changes={
            "runtime_fingerprint": other,
            "runtime_fingerprint_sha256": other_sha,
        }
    )
    # The payload and its own digest are internally consistent; rejection comes
    # from comparing the drill runtime to the runtime evaluating Stage 6A.
    decision = evaluate_stage6a_readiness(
        stage5a=stage5a,
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5a_qualified_observed_pass_missing" in decision.blockers


def test_stage5a_runtime_fingerprint_sha_must_match_nested_runtime_payload() -> None:
    current = capture_runtime_fingerprint().payload()
    other = dict(current)
    other["sqlite_version"] = "0.0.0"
    stage5a = qualified_stage5a(changes={"runtime_fingerprint": other})
    decision = evaluate_stage6a_readiness(
        stage5a=stage5a,
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5a_qualified_observed_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    "changes",
    [
        {"chain_id": 1},
        {"upstream_chain_id": 1},
        {"reset_block": 122},
        {"fork_mine_observed": False},
        {"upstream_verified": False},
        {"fork_block_hash_matches_upstream": False},
        {"prediction_code_matches_upstream": False},
        {"chainlink_code_matches_upstream_after_reset": False},
        {"upstream_fork_block_hash": "0x" + "cd" * 32},
        {"transaction_signed": True},
        {"mainnet_transaction_broadcast": True},
    ],
)
def test_unverified_or_unsafe_stage5b_payload_cannot_clear_gate(changes: dict[str, object]) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(changes=changes),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_qualified_observed_pass_missing" in decision.blockers


def test_generic_observed_shadow_json_cannot_clear_gate_anymore() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(),
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
        qualified_shadow(require_all_decisions_settled=False),
        qualified_shadow(require_fully_costed_claim_or_refund_gas=False),
    ],
)
def test_incomplete_misclassified_or_weakened_shadow_evidence_cannot_clear_gate(
    shadow: Evidence,
) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(),
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
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(**changes),
    )
    assert not decision.ready
    assert blocker in decision.blockers


def test_gate_rechecks_payload_hash_even_for_in_memory_evidence() -> None:
    valid = qualified_stage5a()
    tampered_payload = dict(valid.payload)
    tampered_payload["unresolved_count_final"] = 1
    tampered = Evidence(
        valid.kind,
        valid.origin,
        valid.passed,
        valid.artifact_sha256,
        valid.recorded_at,
        tampered_payload,
    )
    decision = evaluate_stage6a_readiness(
        stage5a=tampered,
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5a_qualified_observed_pass_missing" in decision.blockers


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
