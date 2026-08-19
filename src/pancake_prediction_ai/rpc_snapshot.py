from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .read_only_rpc import ReadOnlyJsonRpcClient, RpcError


@dataclass(frozen=True, slots=True)
class BlockAnchor:
    number: int
    block_hash: str
    timestamp_s: int

    @property
    def rpc_tag(self) -> str:
        return hex(self.number)

    def validate(self) -> None:
        if self.number < 0:
            raise ValueError("block number must be non-negative")
        if self.timestamp_s <= 0:
            raise ValueError("block timestamp must be positive")
        if not isinstance(self.block_hash, str) or not self.block_hash.startswith("0x") or len(self.block_hash) != 66:
            raise ValueError("block hash must be a 32-byte hex string")
        try:
            int(self.block_hash[2:], 16)
        except ValueError as exc:
            raise ValueError("block hash must be hex") from exc


def _hex_int(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RpcError(f"{field} must be a hex string")
    return int(value, 16)


def fetch_block_anchor(client: ReadOnlyJsonRpcClient) -> BlockAnchor:
    """Capture a concrete block and use that number for all subsequent reads."""

    number = client.block_number()
    tag = hex(number)
    result = client.call("eth_getBlockByNumber", [tag, False])
    if not isinstance(result, dict):
        raise RpcError("eth_getBlockByNumber must return an object")
    returned_number = _hex_int(result.get("number"), "block.number")
    if returned_number != number:
        raise RpcError("block number changed inside pinned block response")
    block_hash = result.get("hash")
    timestamp_s = _hex_int(result.get("timestamp"), "block.timestamp")
    anchor = BlockAnchor(number=number, block_hash=str(block_hash), timestamp_s=timestamp_s)
    anchor.validate()
    return anchor


def eth_call_at(
    client: ReadOnlyJsonRpcClient,
    *,
    to: str,
    data: str,
    anchor: BlockAnchor,
) -> str:
    anchor.validate()
    if not isinstance(to, str) or not to.startswith("0x") or len(to) != 42:
        raise ValueError("to must be a 20-byte hex address")
    if not isinstance(data, str) or not data.startswith("0x"):
        raise ValueError("data must be hex calldata")
    result = client.call("eth_call", [{"to": to, "data": data}, anchor.rpc_tag])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RpcError("eth_call result must be hex data")
    return result


def get_code_at(
    client: ReadOnlyJsonRpcClient,
    *,
    address: str,
    anchor: BlockAnchor,
) -> str:
    anchor.validate()
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        raise ValueError("address must be a 20-byte hex address")
    result = client.call("eth_getCode", [address, anchor.rpc_tag])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise RpcError("eth_getCode result must be hex data")
    return result
