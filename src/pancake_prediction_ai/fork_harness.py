from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .evidence_gate import Evidence, EvidenceKind, EvidenceOrigin


RpcCall = Callable[[str, list[Any]], Any]
STAGE5B_FORK_SCHEMA = "stage5b_verified_local_bsc_fork_v1"


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
    upstream_chain_id: int | None = None
    local_initial_block_hash: str | None = None
    upstream_fork_block_hash: str | None = None
    local_reset_block_hash: str | None = None
    fork_block_hash_matches_upstream: bool = False
    reset_block_hash_matches_upstream: bool = False
    prediction_code_matches_upstream: bool = False
    chainlink_code_matches_upstream: bool = False
    prediction_code_matches_upstream_after_reset: bool = False
    chainlink_code_matches_upstream_after_reset: bool = False
    upstream_verified: bool = False

    @property
    def local_probe_passed(self) -> bool:
        return (
            self.chain_id == 56
            and self.initial_block > 0
            and self.mined_block >= self.initial_block + 1
            and self.reset_block == self.initial_block
            and self.prediction_contract_code_present
            and self.chainlink_contract_code_present
            and self.prediction_code_present_after_reset
            and self.chainlink_code_present_after_reset
            and self.fork_reset_supported
            and self.fork_mine_observed
        )

    @property
    def passed(self) -> bool:
        return (
            self.local_probe_passed
            and self.upstream_verified
            and self.upstream_chain_id == 56
            and self.fork_block_hash_matches_upstream
            and self.reset_block_hash_matches_upstream
            and self.prediction_code_matches_upstream
            and self.chainlink_code_matches_upstream
            and self.prediction_code_matches_upstream_after_reset
            and self.chainlink_code_matches_upstream_after_reset
        )


def _hex_int(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("RPC integer result must be hex string")
    return int(value, 16)


def _has_code(value: object) -> bool:
    return isinstance(value, str) and value not in {"0x", "0x0", ""}


def _normalize_hex(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"{field} must be hex string")
    try:
        int(value[2:] or "0", 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be hexadecimal") from exc
    return value.lower()


def _block_hash(rpc: RpcCall, block_number: int) -> str:
    result = rpc("eth_getBlockByNumber", [hex(block_number), False])
    if not isinstance(result, dict):
        raise ValueError("eth_getBlockByNumber must return an object")
    returned_number = _hex_int(result.get("number"))
    if returned_number != block_number:
        raise ValueError(
            f"eth_getBlockByNumber returned block {returned_number}, expected {block_number}"
        )
    block_hash = _normalize_hex(result.get("hash"), field="block.hash")
    if len(block_hash) != 66:
        raise ValueError("block.hash must be 32-byte hex")
    return block_hash


def _probe_local_core(
    rpc: RpcCall,
    *,
    prediction_contract: str,
    chainlink_contract: str,
) -> tuple[dict[str, Any], str, str, str, str]:
    chain_id = _hex_int(rpc("eth_chainId", []))
    initial_block = _hex_int(rpc("eth_blockNumber", []))
    local_initial_hash = _block_hash(rpc, initial_block)
    prediction_code = _normalize_hex(
        rpc("eth_getCode", [prediction_contract, "latest"]),
        field="prediction code",
    )
    chainlink_code = _normalize_hex(
        rpc("eth_getCode", [chainlink_contract, "latest"]),
        field="chainlink code",
    )

    # Local development RPC methods only. No transaction is signed and nothing is broadcast to mainnet.
    mine_result = rpc("evm_mine", [])
    mined_block = _hex_int(rpc("eth_blockNumber", []))
    fork_mine_observed = (
        (mine_result is None or mine_result is True or isinstance(mine_result, str))
        and mined_block >= initial_block + 1
    )

    reset_result = rpc("anvil_reset", [])
    reset_block = _hex_int(rpc("eth_blockNumber", []))
    local_reset_hash = _block_hash(rpc, reset_block)
    prediction_code_after_reset = _normalize_hex(
        rpc("eth_getCode", [prediction_contract, "latest"]),
        field="prediction code after reset",
    )
    chainlink_code_after_reset = _normalize_hex(
        rpc("eth_getCode", [chainlink_contract, "latest"]),
        field="chainlink code after reset",
    )
    fork_reset_supported = reset_result is None or reset_result is True

    core = {
        "chain_id": chain_id,
        "initial_block": initial_block,
        "mined_block": mined_block,
        "reset_block": reset_block,
        "prediction_contract_code_present": _has_code(prediction_code),
        "chainlink_contract_code_present": _has_code(chainlink_code),
        "prediction_code_present_after_reset": _has_code(prediction_code_after_reset),
        "chainlink_code_present_after_reset": _has_code(chainlink_code_after_reset),
        "fork_reset_supported": fork_reset_supported,
        "fork_mine_observed": fork_mine_observed,
    }
    return (
        core,
        local_initial_hash,
        local_reset_hash,
        prediction_code,
        chainlink_code,
        prediction_code_after_reset,
        chainlink_code_after_reset,
    )


def probe_local_bsc_fork(
    rpc: RpcCall,
    *,
    prediction_contract: str,
    chainlink_contract: str,
) -> ForkProbeResult:
    """Run a local-only probe.

    This can test Anvil reset/mine mechanics but cannot prove BSC fork provenance
    by itself. Consequently `result.passed` remains False until an upstream BSC
    RPC comparison is performed by `probe_verified_local_bsc_fork`.
    """

    (
        core,
        local_initial_hash,
        local_reset_hash,
        _prediction_code,
        _chainlink_code,
        _prediction_code_after_reset,
        _chainlink_code_after_reset,
    ) = _probe_local_core(
        rpc,
        prediction_contract=prediction_contract,
        chainlink_contract=chainlink_contract,
    )
    return ForkProbeResult(
        **core,
        local_initial_block_hash=local_initial_hash,
        local_reset_block_hash=local_reset_hash,
    )


def probe_verified_local_bsc_fork(
    local_rpc: RpcCall,
    upstream_rpc: RpcCall,
    *,
    prediction_contract: str,
    chainlink_contract: str,
) -> ForkProbeResult:
    """Verify a local Anvil-style fork against an independent read-only BSC RPC.

    The fork-base block hash and both target bytecodes must match upstream BSC at
    the exact fork block. After a local `evm_mine` and `anvil_reset`, the local
    node must return to the same upstream block hash and bytecode. This avoids
    treating an arbitrary chain-id-56 development chain as Stage 5B evidence.
    """

    (
        core,
        local_initial_hash,
        local_reset_hash,
        local_prediction_code,
        local_chainlink_code,
        local_prediction_after_reset,
        local_chainlink_after_reset,
    ) = _probe_local_core(
        local_rpc,
        prediction_contract=prediction_contract,
        chainlink_contract=chainlink_contract,
    )
    initial_block = int(core["initial_block"])
    upstream_chain_id = _hex_int(upstream_rpc("eth_chainId", []))
    upstream_hash = _block_hash(upstream_rpc, initial_block)
    block_tag = hex(initial_block)
    upstream_prediction_code = _normalize_hex(
        upstream_rpc("eth_getCode", [prediction_contract, block_tag]),
        field="upstream prediction code",
    )
    upstream_chainlink_code = _normalize_hex(
        upstream_rpc("eth_getCode", [chainlink_contract, block_tag]),
        field="upstream chainlink code",
    )

    return ForkProbeResult(
        **core,
        upstream_chain_id=upstream_chain_id,
        local_initial_block_hash=local_initial_hash,
        upstream_fork_block_hash=upstream_hash,
        local_reset_block_hash=local_reset_hash,
        fork_block_hash_matches_upstream=local_initial_hash == upstream_hash,
        reset_block_hash_matches_upstream=(
            int(core["reset_block"]) == initial_block and local_reset_hash == upstream_hash
        ),
        prediction_code_matches_upstream=(
            _has_code(upstream_prediction_code)
            and local_prediction_code == upstream_prediction_code
        ),
        chainlink_code_matches_upstream=(
            _has_code(upstream_chainlink_code)
            and local_chainlink_code == upstream_chainlink_code
        ),
        prediction_code_matches_upstream_after_reset=(
            _has_code(upstream_prediction_code)
            and local_prediction_after_reset == upstream_prediction_code
        ),
        chainlink_code_matches_upstream_after_reset=(
            _has_code(upstream_chainlink_code)
            and local_chainlink_after_reset == upstream_chainlink_code
        ),
        upstream_verified=True,
    )


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def make_stage5b_evidence(
    result: ForkProbeResult,
    *,
    recorded_at: str | None = None,
) -> Evidence:
    payload: dict[str, Any] = {
        "schema": STAGE5B_FORK_SCHEMA,
        "probe_type": "verified_local_bsc_fork",
        "transaction_signed": False,
        "mainnet_transaction_broadcast": False,
        **asdict(result),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return Evidence(
        kind=EvidenceKind.STAGE5B_FORK,
        origin=EvidenceOrigin.OBSERVED,
        passed=result.passed,
        artifact_sha256=digest,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(),
        payload=payload,
    )


def write_stage5b_evidence(evidence: Evidence, path: str | Path) -> Path:
    if evidence.kind is not EvidenceKind.STAGE5B_FORK:
        raise ValueError("Stage 5B writer requires STAGE5B_FORK evidence")
    if evidence.origin is not EvidenceOrigin.OBSERVED:
        raise ValueError("Stage 5B writer requires OBSERVED evidence")
    payload = dict(evidence.payload)
    if hashlib.sha256(_canonical(payload)).hexdigest() != evidence.artifact_sha256:
        raise ValueError("Stage 5B evidence SHA-256 mismatch")
    document = {
        "kind": evidence.kind.value,
        "origin": evidence.origin.value,
        "passed": evidence.passed,
        "artifact_sha256": evidence.artifact_sha256,
        "recorded_at": evidence.recorded_at,
        "payload": payload,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination
