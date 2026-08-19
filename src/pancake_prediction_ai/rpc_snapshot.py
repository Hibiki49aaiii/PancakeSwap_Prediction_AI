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


def fetch_block_anchor_by_number(
    client: ReadOnlyJsonRpcClient,
    block_number: int,
) -> BlockAnchor:
    if block_number < 0:
        raise ValueError("block_number must be non-negative")
    tag = hex(block_number)
    result = client.call("eth_getBlockByNumber", [tag, False])
    if result is None:
        raise RpcError(f"block {block_number} is unavailable from RPC")
    if not isinstance(result, dict):
        raise RpcError("eth_getBlockByNumber must return an object")
    returned_number = _hex_int(result.get("number"), "block.number")
    if returned_number != block_number:
        raise RpcError("block number changed inside pinned block response")
    block_hash = result.get("hash")
    timestamp_s = _hex_int(result.get("timestamp"), "block.timestamp")
    anchor = BlockAnchor(
        number=block_number,
        block_hash=str(block_hash),
        timestamp_s=timestamp_s,
    )
    anchor.validate()
    return anchor


def fetch_block_anchor(client: ReadOnlyJsonRpcClient) -> BlockAnchor:
    """Capture the current concrete block and pin subsequent reads to it."""

    return fetch_block_anchor_by_number(client, client.block_number())


def find_block_at_or_before_timestamp(
    client: ReadOnlyJsonRpcClient,
    *,
    target_timestamp_s: int,
    lower_block: int,
    upper_block: int,
) -> BlockAnchor:
    """Binary-search an RPC block range for the latest block not after target.

    The caller supplies explicit search bounds so this helper never silently
    assumes archive depth or contract deployment history.
    """

    if target_timestamp_s <= 0:
        raise ValueError("target_timestamp_s must be positive")
    if lower_block < 0 or upper_block < lower_block:
        raise ValueError("invalid block search bounds")

    lower_anchor = fetch_block_anchor_by_number(client, lower_block)
    upper_anchor = fetch_block_anchor_by_number(client, upper_block)
    if target_timestamp_s < lower_anchor.timestamp_s:
        raise ValueError("target timestamp predates lower block bound")
    if target_timestamp_s >= upper_anchor.timestamp_s:
        return upper_anchor

    low = lower_block
    high = upper_block
    best = lower_anchor
    while low <= high:
        mid = (low + high) // 2
        anchor = fetch_block_anchor_by_number(client, mid)
        if anchor.timestamp_s <= target_timestamp_s:
            best = anchor
            low = mid + 1
        else:
            high = mid - 1
    return best


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
