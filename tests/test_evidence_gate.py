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
from pancake_prediction_ai.pancake_contract import BNB_PREDICTION_CONTRACT
from pancake_prediction_ai.runtime_fingerprint import (
    capture_runtime_fingerprint,
    fingerprint_sha256,
)


CHAINLINK_ORACLE = "0x2222222222222222222222222222222222222222"
BULL_ACCOUNT = "0x000000000000000000000000000000000000b001"
BEAR_ACCOUNT = "0x000000000000000000000000000000000000b002"
BLOCK_HASH = "0x" + "ab" * 32


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
    fork_changes: dict[str, object] | None = None,
    execution_changes: dict[str, object] | None = None,
    top_changes: dict[str, object] | None = None,
) -> Evidence:
    fork: dict[str, object] = {
        "prediction_contract": BNB_PREDICTION_CONTRACT,
        "chainlink_contract": CHAINLINK_ORACLE,
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
        "local_initial_block_hash": BLOCK_HASH,
        "upstream_fork_block_hash": BLOCK_HASH,
        "local_reset_block_hash": BLOCK_HASH,
        "fork_block_hash_matches_upstream": True,
        "reset_block_hash_matches_upstream": True,
        "prediction_code_matches_upstream": True,
        "chainlink_code_matches_upstream": True,
        "prediction_code_matches_upstream_after_reset": True,
        "chainlink_code_matches_upstream_after_reset": True,
        "upstream_verified": True,
    }
    execution: dict[str, object] = {
        "prediction_contract": BNB_PREDICTION_CONTRACT,
        "fork_base_block": 123,
        "fork_base_block_hash": BLOCK_HASH,
        "epoch": 77,
        "block_timestamp_s": 105,
        "round_start_timestamp_s": 100,
        "round_lock_timestamp_s": 200,
        "min_bet_amount_wei": 100,
        "stake_wei": 100,
        "bull_test_account": BULL_ACCOUNT,
        "bear_test_account": BEAR_ACCOUNT,
        "bettable_window_observed": True,
        "bull_tx_hash": "0x" + "01" * 32,
        "bull_receipt_block": 124,
        "bull_tx_mined_success": True,
        "bull_event_observed": True,
        "bull_ledger_matches": True,
        "bull_pool_delta_matches": True,
        "duplicate_bull_reverted": True,
        "state_restored_after_bull_reset": True,
        "below_minimum_bear_reverted": True,
        "bear_tx_hash": "0x" + "02" * 32,
        "bear_receipt_block": 124,
        "bear_tx_mined_success": True,
        "bear_event_observed": True,
        "bear_ledger_matches": True,
        "bear_pool_delta_matches": True,
        "state_restored_after_bear_reset": True,
        "private_key_used": False,
        "raw_signed_transaction_used": False,
        "mainnet_transaction_broadcast": False,
    }
    if fork_changes:
        fork.update(fork_changes)
    if execution_changes:
        execution.update(execution_changes)
    payload: dict[str, object] = {
        "schema": "stage5b_verified_local_bsc_fork_execution_v3",
        "probe_type": "verified_local_bsc_fork_prediction_execution",
        "execution_transport": "loopback_impersonated_eth_sendTransaction",
        "fork_provenance": fork,
        "prediction_execution": execution,
        "private_key_used": False,
        "raw_signed_transaction_used": False,
        "mainnet_transaction_broadcast": False,
    }
    if top_changes:
        payload.update(top_changes)
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
            "require_fully_costed_claim_or_refund_gas": require_fully_costed_claim_or_refund_gas,
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
        prediction_contract=BNB_PREDICTION_CONTRACT,
        chainlink_oracle=CHAINLINK_ORACLE,
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


def test_stage5b_v2_provenance_only_evidence_no_longer_clears_stage6a() -> None:
    payload: dict[str, object] = {
        "schema": "stage5b_verified_local_bsc_fork_v2",
        "probe_type": "verified_local_bsc_fork",
        "prediction_contract": BNB_PREDICTION_CONTRACT,
        "chainlink_contract": CHAINLINK_ORACLE,
        "transaction_signed": False,
        "mainnet_transaction_broadcast": False,
    }
    old = Evidence(
        EvidenceKind.STAGE5B_FORK,
        EvidenceOrigin.OBSERVED,
        True,
        _digest(payload),
        "2026-08-19T00:00:00+09:00",
        payload,
    )
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=old,
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
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
        {"prediction_contract": "0x3333333333333333333333333333333333333333"},
        {"chainlink_contract": "0x4444444444444444444444444444444444444444"},
        {"chain_id": 1},
        {"upstream_chain_id": 1},
        {"reset_block": 122},
        {"fork_mine_observed": False},
        {"upstream_verified": False},
        {"fork_block_hash_matches_upstream": False},
        {"prediction_code_matches_upstream": False},
        {"chainlink_code_matches_upstream_after_reset": False},
        {"upstream_fork_block_hash": "0x" + "cd" * 32},
    ],
)
def test_unverified_or_misbound_stage5b_provenance_cannot_clear_gate(changes: dict[str, object]) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(fork_changes=changes),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_qualified_observed_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    "changes",
    [
        {"prediction_contract": "0x3333333333333333333333333333333333333333"},
        {"fork_base_block": 122},
        {"fork_base_block_hash": "0x" + "cd" * 32},
        {"round_lock_timestamp_s": 105},
        {"min_bet_amount_wei": 0},
        {"stake_wei": 99},
        {"bear_test_account": BULL_ACCOUNT},
        {"bull_tx_hash": "0x" + "02" * 32},
        {"bull_receipt_block": 123},
        {"bear_receipt_block": 123},
        {"bull_tx_mined_success": False},
        {"bull_event_observed": False},
        {"bull_ledger_matches": False},
        {"bull_pool_delta_matches": False},
        {"duplicate_bull_reverted": False},
        {"state_restored_after_bull_reset": False},
        {"below_minimum_bear_reverted": False},
        {"bear_tx_mined_success": False},
        {"bear_event_observed": False},
        {"bear_ledger_matches": False},
        {"bear_pool_delta_matches": False},
        {"state_restored_after_bear_reset": False},
        {"private_key_used": True},
        {"raw_signed_transaction_used": True},
        {"mainnet_transaction_broadcast": True},
    ],
)
def test_incomplete_or_unsafe_stage5b_execution_cannot_clear_gate(changes: dict[str, object]) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(execution_changes=changes),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_qualified_observed_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_transport": "remote_rpc"},
        {"private_key_used": True},
        {"raw_signed_transaction_used": True},
        {"mainnet_transaction_broadcast": True},
    ],
)
def test_unsafe_stage5b_top_level_claims_cannot_clear_gate(changes: dict[str, object]) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(top_changes=changes),
        shadow=qualified_shadow(),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_qualified_observed_pass_missing" in decision.blockers


def test_stage5b_evidence_must_match_runtime_chainlink_binding() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=qualified_stage5a(),
        stage5b=qualified_stage5b(),
        shadow=qualified_shadow(),
        safety=safety(chainlink_oracle="0x4444444444444444444444444444444444444444"),
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
        ({"prediction_contract": "0x3333333333333333333333333333333333333333"}, "prediction_contract_binding_failed"),
        ({"chainlink_oracle": "not-an-address"}, "chainlink_oracle_binding_failed"),
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
