from __future__ import annotations

from pancake_prediction_ai.evidence_gate import EvidenceKind, EvidenceOrigin
from pancake_prediction_ai.fork_harness import make_stage5b_evidence, probe_local_bsc_fork


def test_probe_passes_on_bsc_fork_with_contract_code() -> None:
    responses = {
        "eth_chainId": "0x38",
        "eth_blockNumber": "0x1234",
        "anvil_reset": True,
        "evm_mine": True,
    }

    def rpc(method: str, params: list[object]) -> object:
        if method == "eth_getCode":
            return "0x60016000"
        return responses[method]

    result = probe_local_bsc_fork(
        rpc,
        prediction_contract="0x1111111111111111111111111111111111111111",
        chainlink_contract="0x2222222222222222222222222222222222222222",
    )
    assert result.passed

    evidence = make_stage5b_evidence(result, recorded_at="2026-08-19T08:00:00+09:00")
    assert evidence.kind is EvidenceKind.STAGE5B_FORK
    assert evidence.origin is EvidenceOrigin.OBSERVED
    assert evidence.passed
    assert len(evidence.artifact_sha256) == 64


def test_wrong_chain_id_fails_even_when_rpc_methods_exist() -> None:
    def rpc(method: str, params: list[object]) -> object:
        values = {
            "eth_chainId": "0x1",
            "eth_blockNumber": "0x1234",
            "anvil_reset": True,
            "evm_mine": True,
        }
        if method == "eth_getCode":
            return "0x6000"
        return values[method]

    result = probe_local_bsc_fork(
        rpc,
        prediction_contract="0x1",
        chainlink_contract="0x2",
    )
    assert not result.passed


def test_missing_contract_code_fails_probe() -> None:
    calls = 0

    def rpc(method: str, params: list[object]) -> object:
        nonlocal calls
        if method == "eth_chainId":
            return "0x38"
        if method == "eth_blockNumber":
            return "0x1234"
        if method == "eth_getCode":
            calls += 1
            return "0x" if calls == 1 else "0x6000"
        return True

    result = probe_local_bsc_fork(
        rpc,
        prediction_contract="0x1",
        chainlink_contract="0x2",
    )
    assert not result.passed
    assert not result.prediction_contract_code_present
