from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .evidence_gate import Evidence, EvidenceKind, EvidenceOrigin


RpcCall = Callable[[str, list[Any]], Any]


@dataclass(frozen=True, slots=True)
class ForkProbeResult:
    chain_id: int
    initial_block: int
    mined_block: int
    reset_block: int
    prediction_contract_code_present: bool
    chainlink_contract_code_present: bool
    prediction_code_present_after_reset: bool
    chainlink_code_present_after_reset: bool
    fork_reset_supported: bool
    fork_mine_observed: bool

    @property
    def passed(self) -> bool:
        return (
            self.chain_id == 56
            and self.initial_block > 0
            and self.mined_block >= self.initial_block + 1
            and self.reset_block > 0
            and self.prediction_contract_code_present
            and self.chainlink_contract_code_present
            and self.prediction_code_present_after_reset
            and self.chainlink_code_present_after_reset
            and self.fork_reset_supported
            and self.fork_mine_observed
        )


def _hex_int(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("RPC integer result must be hex string")
    return int(value, 16)


def _has_code(value: object) -> bool:
    return isinstance(value, str) and value not in {"0x", "0x0", ""}


def probe_local_bsc_fork(
    rpc: RpcCall,
    *,
    prediction_contract: str,
    chainlink_contract: str,
) -> ForkProbeResult:
    chain_id = _hex_int(rpc("eth_chainId", []))
    initial_block = _hex_int(rpc("eth_blockNumber", []))
    prediction_code = rpc("eth_getCode", [prediction_contract, "latest"])
    chainlink_code = rpc("eth_getCode", [chainlink_contract, "latest"])

    # Local development RPC methods only. No transaction is signed and nothing is broadcast to mainnet.
    mine_result = rpc("evm_mine", [])
    mined_block = _hex_int(rpc("eth_blockNumber", []))
    fork_mine_observed = (
        (mine_result is None or mine_result is True or isinstance(mine_result, str))
        and mined_block >= initial_block + 1
    )

    reset_result = rpc("anvil_reset", [])
    reset_block = _hex_int(rpc("eth_blockNumber", []))
    prediction_code_after_reset = rpc("eth_getCode", [prediction_contract, "latest"])
    chainlink_code_after_reset = rpc("eth_getCode", [chainlink_contract, "latest"])
    fork_reset_supported = reset_result is None or reset_result is True

    return ForkProbeResult(
        chain_id=chain_id,
        initial_block=initial_block,
        mined_block=mined_block,
        reset_block=reset_block,
        prediction_contract_code_present=_has_code(prediction_code),
        chainlink_contract_code_present=_has_code(chainlink_code),
        prediction_code_present_after_reset=_has_code(prediction_code_after_reset),
        chainlink_code_present_after_reset=_has_code(chainlink_code_after_reset),
        fork_reset_supported=fork_reset_supported,
        fork_mine_observed=fork_mine_observed,
    )


def make_stage5b_evidence(result: ForkProbeResult, *, recorded_at: str | None = None) -> Evidence:
    payload: Mapping[str, Any] = {
        "chain_id": result.chain_id,
        "initial_block": result.initial_block,
        "mined_block": result.mined_block,
        "reset_block": result.reset_block,
        "prediction_contract_code_present": result.prediction_contract_code_present,
        "chainlink_contract_code_present": result.chainlink_contract_code_present,
        "prediction_code_present_after_reset": result.prediction_code_present_after_reset,
        "chainlink_code_present_after_reset": result.chainlink_code_present_after_reset,
        "fork_reset_supported": result.fork_reset_supported,
        "fork_mine_observed": result.fork_mine_observed,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return Evidence(
        kind=EvidenceKind.STAGE5B_FORK,
        origin=EvidenceOrigin.OBSERVED,
        passed=result.passed,
        artifact_sha256=digest,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(),
        payload=payload,
    )
