from __future__ import annotations

from dataclasses import replace

from pancake_prediction_ai.evidence_gate import EvidenceKind, EvidenceOrigin
from pancake_prediction_ai.fork_execution import Stage5BExecutionResult
from pancake_prediction_ai.fork_harness import ForkProbeResult
from pancake_prediction_ai.pancake_contract import BNB_PREDICTION_CONTRACT
from pancake_prediction_ai.stage5b_evidence import make_stage5b_execution_evidence


BLOCK_HASH = "0x" + "ab" * 32
CHAINLINK = "0x2222222222222222222222222222222222222222"


def fork_result() -> ForkProbeResult:
    return ForkProbeResult(
        prediction_contract=BNB_PREDICTION_CONTRACT,
        chainlink_contract=CHAINLINK,
        chain_id=56,
        initial_block=123,
        mined_block=124,
        reset_block=123,
        prediction_contract_code_present=True,
        chainlink_contract_code_present=True,
        prediction_code_present_after_reset=True,
        chainlink_code_present_after_reset=True,
        fork_reset_supported=True,
        fork_mine_observed=True,
        upstream_chain_id=56,
        local_initial_block_hash=BLOCK_HASH,
        upstream_fork_block_hash=BLOCK_HASH,
        local_reset_block_hash=BLOCK_HASH,
        fork_block_hash_matches_upstream=True,
        reset_block_hash_matches_upstream=True,
        prediction_code_matches_upstream=True,
        chainlink_code_matches_upstream=True,
        prediction_code_matches_upstream_after_reset=True,
        chainlink_code_matches_upstream_after_reset=True,
        upstream_verified=True,
    )


def execution_result() -> Stage5BExecutionResult:
    return Stage5BExecutionResult(
        prediction_contract=BNB_PREDICTION_CONTRACT,
        fork_base_block=123,
        fork_base_block_hash=BLOCK_HASH,
        epoch=77,
        block_timestamp_s=105,
        round_start_timestamp_s=100,
        round_lock_timestamp_s=200,
        min_bet_amount_wei=100,
        stake_wei=100,
        bull_test_account="0x000000000000000000000000000000000000b001",
        bear_test_account="0x000000000000000000000000000000000000b002",
        bettable_window_observed=True,
        bull_tx_hash="0x" + "01" * 32,
        bull_receipt_block=124,
        bull_tx_mined_success=True,
        bull_event_observed=True,
        bull_ledger_matches=True,
        bull_pool_delta_matches=True,
        duplicate_bull_reverted=True,
        state_restored_after_bull_reset=True,
        below_minimum_bear_reverted=True,
        bear_tx_hash="0x" + "02" * 32,
        bear_receipt_block=124,
        bear_tx_mined_success=True,
        bear_event_observed=True,
        bear_ledger_matches=True,
        bear_pool_delta_matches=True,
        state_restored_after_bear_reset=True,
    )


def test_v3_evidence_passes_only_when_verified_fork_and_execution_lineage_match() -> None:
    evidence = make_stage5b_execution_evidence(
        fork_result(),
        execution_result(),
        recorded_at="2026-08-19T22:35:00+09:00",
    )
    assert evidence.kind is EvidenceKind.STAGE5B_FORK
    assert evidence.origin is EvidenceOrigin.OBSERVED
    assert evidence.passed
    assert evidence.payload["schema"] == "stage5b_verified_local_bsc_fork_execution_v3"
    assert evidence.payload["execution_transport"] == "loopback_impersonated_eth_sendTransaction"
    assert evidence.payload["private_key_used"] is False
    assert evidence.payload["raw_signed_transaction_used"] is False
    assert evidence.payload["mainnet_transaction_broadcast"] is False
    assert len(evidence.artifact_sha256) == 64


def test_v3_evidence_rejects_execution_for_different_prediction_contract() -> None:
    execution = replace(
        execution_result(),
        prediction_contract="0x3333333333333333333333333333333333333333",
    )
    evidence = make_stage5b_execution_evidence(fork_result(), execution)
    assert not evidence.passed


def test_v3_evidence_rejects_execution_from_different_fork_base_block() -> None:
    execution = replace(execution_result(), fork_base_block=122)
    evidence = make_stage5b_execution_evidence(fork_result(), execution)
    assert not evidence.passed


def test_v3_evidence_rejects_execution_from_different_fork_base_hash() -> None:
    execution = replace(execution_result(), fork_base_block_hash="0x" + "cd" * 32)
    evidence = make_stage5b_execution_evidence(fork_result(), execution)
    assert not evidence.passed


def test_v3_evidence_rejects_failed_execution_even_with_verified_provenance() -> None:
    execution = replace(execution_result(), bear_event_observed=False)
    evidence = make_stage5b_execution_evidence(fork_result(), execution)
    assert not evidence.passed


def test_v3_evidence_rejects_unverified_fork_even_with_successful_execution() -> None:
    fork = replace(fork_result(), upstream_verified=False)
    evidence = make_stage5b_execution_evidence(fork, execution_result())
    assert not evidence.passed
