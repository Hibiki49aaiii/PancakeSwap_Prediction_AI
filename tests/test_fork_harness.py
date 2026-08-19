from __future__ import annotations

from pancake_prediction_ai.evidence_gate import EvidenceKind, EvidenceOrigin
from pancake_prediction_ai.fork_harness import (
    make_stage5b_evidence,
    probe_local_bsc_fork,
    probe_verified_local_bsc_fork,
)


BLOCK_HASH = "0x" + "ab" * 32
OTHER_BLOCK_HASH = "0x" + "cd" * 32
BYTECODE = "0x60016000"
PREDICTION = "0x1111111111111111111111111111111111111111"
CHAINLINK = "0x2222222222222222222222222222222222222222"


class FakeForkRpc:
    def __init__(
        self,
        *,
        chain_id: int = 56,
        missing_prediction_code: bool = False,
        mine_advances: bool = True,
        block_hash: str = BLOCK_HASH,
        code: str = BYTECODE,
    ) -> None:
        self.chain_id = chain_id
        self.block = 0x1234
        self.base_block = self.block
        self.missing_prediction_code = missing_prediction_code
        self.mine_advances = mine_advances
        self.block_hash = block_hash
        self.code = code
        self.code_reads = 0

    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(self.block)
        if method == "eth_getBlockByNumber":
            requested = int(str(params[0]), 16)
            return {"number": hex(requested), "hash": self.block_hash}
        if method == "eth_getCode":
            self.code_reads += 1
            if self.missing_prediction_code and self.code_reads in {1, 3}:
                return "0x"
            return self.code
        if method == "evm_mine":
            if self.mine_advances:
                self.block += 1
            return True
        if method == "anvil_reset":
            self.block = self.base_block
            return True
        raise KeyError(method)


class FakeUpstreamRpc:
    def __init__(
        self,
        *,
        chain_id: int = 56,
        block_hash: str = BLOCK_HASH,
        code: str = BYTECODE,
    ) -> None:
        self.chain_id = chain_id
        self.block_hash = block_hash
        self.code = code

    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_getBlockByNumber":
            requested = int(str(params[0]), 16)
            return {"number": hex(requested), "hash": self.block_hash}
        if method == "eth_getCode":
            return self.code
        raise KeyError(method)


def test_local_probe_passes_mechanics_but_does_not_clear_stage5b_evidence() -> None:
    result = probe_local_bsc_fork(
        FakeForkRpc(),
        prediction_contract=PREDICTION,
        chainlink_contract=CHAINLINK,
    )
    assert result.passed
    assert result.local_probe_passed
    assert not result.verified_passed
    assert result.prediction_contract == PREDICTION
    assert result.chainlink_contract == CHAINLINK
    assert result.mined_block == result.initial_block + 1
    assert result.reset_block == result.initial_block

    evidence = make_stage5b_evidence(result, recorded_at="2026-08-19T08:00:00+09:00")
    assert evidence.kind is EvidenceKind.STAGE5B_FORK
    assert evidence.origin is EvidenceOrigin.OBSERVED
    assert not evidence.passed
    assert len(evidence.artifact_sha256) == 64


def test_verified_probe_requires_upstream_bsc_hash_bytecode_and_target_addresses() -> None:
    result = probe_verified_local_bsc_fork(
        FakeForkRpc(),
        FakeUpstreamRpc(),
        prediction_contract=PREDICTION,
        chainlink_contract=CHAINLINK,
    )
    assert result.passed
    assert result.verified_passed
    assert result.upstream_verified
    assert result.fork_block_hash_matches_upstream
    assert result.reset_block_hash_matches_upstream
    assert result.prediction_code_matches_upstream
    assert result.chainlink_code_matches_upstream

    evidence = make_stage5b_evidence(result, recorded_at="2026-08-19T08:00:00+09:00")
    assert evidence.passed
    assert evidence.payload["schema"] == "stage5b_verified_local_bsc_fork_v2"
    assert evidence.payload["prediction_contract"] == PREDICTION
    assert evidence.payload["chainlink_contract"] == CHAINLINK
    assert evidence.payload["transaction_signed"] is False
    assert evidence.payload["mainnet_transaction_broadcast"] is False


def test_verified_probe_rejects_arbitrary_chain_id_56_dev_chain_by_block_hash() -> None:
    result = probe_verified_local_bsc_fork(
        FakeForkRpc(block_hash=OTHER_BLOCK_HASH),
        FakeUpstreamRpc(block_hash=BLOCK_HASH),
        prediction_contract=PREDICTION,
        chainlink_contract=CHAINLINK,
    )
    assert result.local_probe_passed
    assert not result.verified_passed
    assert not result.fork_block_hash_matches_upstream
    assert not result.reset_block_hash_matches_upstream
    assert not make_stage5b_evidence(result).passed


def test_verified_probe_rejects_upstream_bytecode_mismatch() -> None:
    result = probe_verified_local_bsc_fork(
        FakeForkRpc(code=BYTECODE),
        FakeUpstreamRpc(code="0x60026000"),
        prediction_contract=PREDICTION,
        chainlink_contract=CHAINLINK,
    )
    assert result.local_probe_passed
    assert not result.verified_passed
    assert not result.prediction_code_matches_upstream
    assert not result.chainlink_code_matches_upstream


def test_verified_probe_requires_full_20_byte_target_addresses() -> None:
    result = probe_verified_local_bsc_fork(
        FakeForkRpc(),
        FakeUpstreamRpc(),
        prediction_contract="0x1",
        chainlink_contract="0x2",
    )
    assert result.local_probe_passed
    assert not result.verified_passed
    assert not make_stage5b_evidence(result).passed


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
