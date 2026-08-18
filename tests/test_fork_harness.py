from __future__ import annotations

from pancake_prediction_ai.evidence_gate import EvidenceKind, EvidenceOrigin
from pancake_prediction_ai.fork_harness import make_stage5b_evidence, probe_local_bsc_fork


class FakeForkRpc:
    def __init__(self, *, chain_id: int = 56, missing_prediction_code: bool = False, mine_advances: bool = True) -> None:
        self.chain_id = chain_id
        self.block = 0x1234
        self.base_block = self.block
        self.missing_prediction_code = missing_prediction_code
        self.mine_advances = mine_advances
        self.code_reads = 0

    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(self.block)
        if method == "eth_getCode":
            self.code_reads += 1
            if self.missing_prediction_code and self.code_reads in {1, 3}:
                return "0x"
            return "0x60016000"
        if method == "evm_mine":
            if self.mine_advances:
                self.block += 1
            return True
        if method == "anvil_reset":
            self.block = self.base_block
            return True
        raise KeyError(method)


def test_probe_passes_on_bsc_fork_with_observed_mutation_and_contract_code() -> None:
    result = probe_local_bsc_fork(
        FakeForkRpc(),
        prediction_contract="0x1111111111111111111111111111111111111111",
        chainlink_contract="0x2222222222222222222222222222222222222222",
    )
    assert result.passed
    assert result.mined_block == result.initial_block + 1
    assert result.reset_block == result.initial_block

    evidence = make_stage5b_evidence(result, recorded_at="2026-08-19T08:00:00+09:00")
    assert evidence.kind is EvidenceKind.STAGE5B_FORK
    assert evidence.origin is EvidenceOrigin.OBSERVED
    assert evidence.passed
    assert len(evidence.artifact_sha256) == 64


def test_wrong_chain_id_fails_even_when_local_methods_work() -> None:
    result = probe_local_bsc_fork(
        FakeForkRpc(chain_id=1),
        prediction_contract="0x1",
        chainlink_contract="0x2",
    )
    assert not result.passed


def test_missing_prediction_contract_code_fails_before_and_after_reset() -> None:
    result = probe_local_bsc_fork(
        FakeForkRpc(missing_prediction_code=True),
        prediction_contract="0x1",
        chainlink_contract="0x2",
    )
    assert not result.passed
    assert not result.prediction_contract_code_present
    assert not result.prediction_code_present_after_reset


def test_rpc_claim_without_actual_block_increment_does_not_count_as_fork_mine() -> None:
    result = probe_local_bsc_fork(
        FakeForkRpc(mine_advances=False),
        prediction_contract="0x1",
        chainlink_contract="0x2",
    )
    assert not result.passed
    assert not result.fork_mine_observed
