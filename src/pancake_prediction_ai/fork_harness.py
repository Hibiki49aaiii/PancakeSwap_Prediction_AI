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
    latest_block: int
    prediction_contract_code_present: bool
    chainlink_contract_code_present: bool
    fork_reset_supported: bool
    fork_mine_supported: bool

    @property
    def passed(self) -> bool:
        return (
            self.chain_id == 56
            and self.latest_block > 0
            and self.prediction_contract_code_present
            and self.chainlink_contract_code_present
            and self.fork_reset_supported
            and self.fork_mine_supported
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
    latest_block = _hex_int(rpc("eth_blockNumber", []))
    prediction_code = rpc("eth_getCode", [prediction_contract, "latest"])
    chainlink_code = rpc("eth_getCode", [chainlink_contract, "latest"])

    # Anvil/Hardhat-compatible local development methods only. These do not sign or broadcast to mainnet.
    reset_result = rpc("anvil_reset", [])
    mine_result = rpc("evm_mine", [])

    return ForkProbeResult(
        chain_id=chain_id,
        latest_block=latest_block,
        prediction_contract_code_present=_has_code(prediction_code),
        chainlink_contract_code_present=_has_code(chainlink_code),
        fork_reset_supported=reset_result is None or reset_result is True,
        fork_mine_supported=mine_result is None or mine_result is True or isinstance(mine_result, str),
    )


def make_stage5b_evidence(result: ForkProbeResult, *, recorded_at: str | None = None) -> Evidence:
    payload: Mapping[str, Any] = {
        "chain_id": result.chain_id,
        "latest_block": result.latest_block,
        "prediction_contract_code_present": result.prediction_contract_code_present,
        "chainlink_contract_code_present": result.chainlink_contract_code_present,
        "fork_reset_supported": result.fork_reset_supported,
        "fork_mine_supported": result.fork_mine_supported,
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
