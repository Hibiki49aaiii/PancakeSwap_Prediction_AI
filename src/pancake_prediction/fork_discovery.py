from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import CHAIN_ID_BSC, MARKETS
from .prediction_preflight import CURRENT_EPOCH_SELECTOR, ROUNDS_SELECTOR


class ForkSourceRpc(Protocol):
    def chain_id(self) -> int: ...

    def block_number(self) -> int: ...

    def block(self, number: int) -> dict[str, Any]: ...

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str: ...


@dataclass(frozen=True, slots=True)
class ForkPoint:
    block_number: int
    block_timestamp: int
    epoch: int
    round_start_timestamp: int
    round_lock_timestamp: int


def _quantity(value: object) -> int:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _decode_words(result: str, expected_words: int) -> tuple[int, ...]:
    raw = result.removeprefix("0x")
    if len(raw) != expected_words * 64:
        raise ValueError(
            f"unexpected ABI result length: expected {expected_words} words, "
            f"got {len(raw) // 64}"
        )
    try:
        return tuple(
            int(raw[index * 64 : (index + 1) * 64], 16)
            for index in range(expected_words)
        )
    except ValueError as exc:
        raise ValueError("ABI result is not hexadecimal") from exc


def _encode_uint256_word(value: int) -> str:
    if value < 0 or value >= 1 << 256:
        raise ValueError("uint256 value is out of range")
    return f"{value:064x}"


def _inspect_candidate(
    rpc: ForkSourceRpc,
    *,
    market: str,
    block_number: int,
) -> ForkPoint | None:
    target = MARKETS[market].address
    block = rpc.block(block_number)
    block_timestamp = _quantity(block["timestamp"])
    epoch = _decode_words(
        rpc.eth_call(target, CURRENT_EPOCH_SELECTOR, block_number),
        1,
    )[0]
    if epoch <= 0:
        return None
    round_words = _decode_words(
        rpc.eth_call(
            target,
            ROUNDS_SELECTOR + _encode_uint256_word(epoch),
            block_number,
        ),
        14,
    )
    round_epoch = round_words[0]
    start_timestamp = round_words[1]
    lock_timestamp = round_words[2]
    if round_epoch != epoch:
        return None
    if start_timestamp <= 0 or lock_timestamp <= start_timestamp:
        return None
    if not start_timestamp < block_timestamp < lock_timestamp:
        return None
    return ForkPoint(
        block_number=block_number,
        block_timestamp=block_timestamp,
        epoch=epoch,
        round_start_timestamp=start_timestamp,
        round_lock_timestamp=lock_timestamp,
    )


def discover_fork_block(
    rpc: ForkSourceRpc,
    *,
    market: str,
    lookback_blocks: int,
    confirmation_lag: int,
) -> ForkPoint:
    if market not in MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if lookback_blocks < 1:
        raise ValueError("lookback_blocks must be positive")
    if confirmation_lag < 1:
        raise ValueError("confirmation_lag must be positive")
    if rpc.chain_id() != CHAIN_ID_BSC:
        raise RuntimeError("fork source RPC is not BSC mainnet chain id 56")

    safe_head = rpc.block_number() - confirmation_lag
    if safe_head <= 0:
        raise RuntimeError("fork source head is too low")
    lower_bound = max(1, safe_head - lookback_blocks + 1)

    for block_number in range(safe_head, lower_bound - 1, -1):
        point = _inspect_candidate(
            rpc,
            market=market,
            block_number=block_number,
        )
        if point is not None:
            return point

    raise RuntimeError(
        "no confirmed bettable Prediction state found "
        f"in the last {lookback_blocks} blocks"
    )
