from __future__ import annotations

from typing import Any, Protocol

from .abi import PREDICTION_EVENTS
from .contracts import CHAIN_ID_BSC, MARKETS


class ForkSourceRpc(Protocol):
    def chain_id(self) -> int: ...

    def block_number(self) -> int: ...

    def block(self, number: int) -> dict[str, Any]: ...

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]: ...


def _quantity(value: object) -> int:
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _start_round_topic() -> str:
    return next(spec.topic0 for spec in PREDICTION_EVENTS if spec.name == "StartRound")


def _first_later_timestamp_block(
    rpc: ForkSourceRpc,
    *,
    start_round_block: int,
    safe_head: int,
    max_scan_blocks: int = 16,
) -> int | None:
    start_timestamp = _quantity(rpc.block(start_round_block)["timestamp"])
    upper = min(safe_head, start_round_block + max_scan_blocks)
    for block_number in range(start_round_block + 1, upper + 1):
        timestamp = _quantity(rpc.block(block_number)["timestamp"])
        if timestamp > start_timestamp:
            return block_number
    return None


def discover_fork_block(
    rpc: ForkSourceRpc,
    *,
    market: str,
    lookback_blocks: int,
    confirmation_lag: int,
    chunk_size: int,
) -> tuple[int, int]:
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if lookback_blocks < 1:
        raise ValueError("lookback_blocks must be positive")
    if confirmation_lag < 1:
        raise ValueError("confirmation_lag must be positive")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if rpc.chain_id() != CHAIN_ID_BSC:
        raise RuntimeError("fork source RPC is not BSC mainnet chain id 56")

    safe_head = rpc.block_number() - confirmation_lag
    if safe_head <= 0:
        raise RuntimeError("fork source head is too low")
    lower_bound = max(0, safe_head - lookback_blocks)
    market_address = MARKETS[market].address
    topic = _start_round_topic()

    cursor = safe_head
    while cursor >= lower_bound:
        start = max(lower_bound, cursor - chunk_size + 1)
        logs = rpc.get_logs(
            market_address,
            start,
            cursor,
            topic0s=(topic,),
        )
        candidates = sorted(
            logs,
            key=lambda log: (
                _quantity(log["blockNumber"]),
                _quantity(log.get("logIndex", 0)),
            ),
            reverse=True,
        )
        for candidate in candidates:
            start_round_block = _quantity(candidate["blockNumber"])
            fork_block = _first_later_timestamp_block(
                rpc,
                start_round_block=start_round_block,
                safe_head=safe_head,
            )
            if fork_block is not None:
                return fork_block, start_round_block
        if start == lower_bound:
            break
        cursor = start - 1

    raise RuntimeError(
        "no confirmed StartRound with a later-timestamp fork block found "
        f"in the last {lookback_blocks} blocks"
    )
