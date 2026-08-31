from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .abi import decode_address_result, function_selector
from .contracts import CHAIN_ID_BSC, Market
from .rpc import RpcError

ORACLE_SELECTOR = function_selector("oracle()")


class HistoricalStateRpc(Protocol):
    def chain_id(self) -> int: ...
    def block(self, number: int) -> dict[str, Any]: ...
    def get_code(self, address: str, block: int | str = "latest") -> str: ...
    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str: ...


@dataclass(frozen=True, slots=True)
class ArchiveProbeResult:
    chain_id: int
    market: str
    block_number: int
    block_hash: str
    block_timestamp: int
    oracle_address: str
    prediction_code_present: bool
    oracle_code_present: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _code_present(code: str) -> bool:
    return code.lower() not in {"", "0x", "0x0"}


def _parse_hex_int(value: object, *, field: str) -> int:
    if not isinstance(value, str):
        raise RpcError(f"historical block missing hex field: {field}")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise RpcError(f"historical block has invalid hex field: {field}") from exc


def probe_archive_state(
    rpc: HistoricalStateRpc,
    market: Market,
    block_number: int,
) -> ArchiveProbeResult:
    if block_number < 0:
        raise ValueError("block_number must be non-negative")

    chain_id = rpc.chain_id()
    if chain_id != CHAIN_ID_BSC:
        raise RpcError(f"expected BSC chain_id={CHAIN_ID_BSC}, got {chain_id}")

    block = rpc.block(block_number)
    returned_number = _parse_hex_int(block.get("number"), field="number")
    if returned_number != block_number:
        raise RpcError(
            f"historical block mismatch: requested {block_number}, got {returned_number}"
        )
    block_hash = block.get("hash")
    if not isinstance(block_hash, str) or not block_hash.startswith("0x"):
        raise RpcError("historical block missing hash")
    block_timestamp = _parse_hex_int(block.get("timestamp"), field="timestamp")

    prediction_code = rpc.get_code(market.address, block_number)
    if not _code_present(prediction_code):
        raise RpcError(
            f"prediction contract has no code at block {block_number}; "
            "choose a block at or after deployment"
        )

    try:
        oracle_raw = rpc.eth_call(market.address, ORACLE_SELECTOR, block_number)
    except RpcError as exc:
        raise RpcError(
            f"historical state unavailable at block {block_number}; "
            "an archive-capable BSC RPC is required"
        ) from exc
    oracle_address = decode_address_result(oracle_raw)
    oracle_code = rpc.get_code(oracle_address, block_number)
    if not _code_present(oracle_code):
        raise RpcError(
            f"oracle contract has no historical code at block {block_number}: "
            f"{oracle_address}"
        )

    return ArchiveProbeResult(
        chain_id=chain_id,
        market=market.symbol,
        block_number=block_number,
        block_hash=block_hash,
        block_timestamp=block_timestamp,
        oracle_address=oracle_address,
        prediction_code_present=True,
        oracle_code_present=True,
    )
