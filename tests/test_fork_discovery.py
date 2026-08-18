from __future__ import annotations

from typing import Any

import pytest

from pancake_prediction.fork_discovery import ForkPoint, discover_fork_block
from pancake_prediction.prediction_preflight import CURRENT_EPOCH_SELECTOR, ROUNDS_SELECTOR


def _word(value: int) -> str:
    return f"{value:064x}"


class _FakeRpc:
    def __init__(
        self,
        *,
        chain_id: int = 56,
        head: int = 110,
        timestamps: dict[int, int] | None = None,
        rounds: dict[int, tuple[int, int, int]] | None = None,
    ) -> None:
        self._chain_id = chain_id
        self._head = head
        self._timestamps = timestamps or {}
        self._rounds = rounds or {}

    def chain_id(self) -> int:
        return self._chain_id

    def block_number(self) -> int:
        return self._head

    def block(self, number: int) -> dict[str, Any]:
        return {"timestamp": hex(self._timestamps.get(number, number))}

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to
        assert isinstance(block, int)
        epoch, start_timestamp, lock_timestamp = self._rounds.get(
            block,
            (0, 0, 0),
        )
        if data == CURRENT_EPOCH_SELECTOR:
            return "0x" + _word(epoch)
        if data.startswith(ROUNDS_SELECTOR):
            requested_epoch = int(data[len(ROUNDS_SELECTOR) :], 16)
            if requested_epoch != epoch:
                return "0x" + "0" * (14 * 64)
            values = [epoch, start_timestamp, lock_timestamp] + [0] * 11
            return "0x" + "".join(_word(value) for value in values)
        raise AssertionError(f"unexpected eth_call selector: {data}")


def test_discovery_prefers_newest_confirmed_bettable_state() -> None:
    rpc = _FakeRpc(
        timestamps={106: 1_100, 105: 1_099},
        rounds={
            106: (700, 1_000, 1_300),
            105: (700, 1_000, 1_300),
        },
    )
    point = discover_fork_block(
        rpc,
        market="BNBUSD",
        lookback_blocks=20,
        confirmation_lag=4,
    )
    assert point == ForkPoint(
        block_number=106,
        block_timestamp=1_100,
        epoch=700,
        round_start_timestamp=1_000,
        round_lock_timestamp=1_300,
    )


def test_discovery_skips_safe_head_outside_betting_window() -> None:
    rpc = _FakeRpc(
        timestamps={106: 1_300, 105: 1_299},
        rounds={
            106: (700, 1_000, 1_300),
            105: (700, 1_000, 1_300),
        },
    )
    point = discover_fork_block(
        rpc,
        market="BNBUSD",
        lookback_blocks=20,
        confirmation_lag=4,
    )
    assert point.block_number == 105
    assert point.block_timestamp == 1_299


def test_discovery_skips_uninitialized_or_mismatched_round_state() -> None:
    rpc = _FakeRpc(
        timestamps={106: 1_100, 105: 1_099, 104: 1_098},
        rounds={
            106: (0, 0, 0),
            105: (701, 0, 0),
            104: (700, 1_000, 1_300),
        },
    )
    point = discover_fork_block(
        rpc,
        market="BNBUSD",
        lookback_blocks=20,
        confirmation_lag=4,
    )
    assert point.block_number == 104
    assert point.epoch == 700


def test_discovery_rejects_non_bsc_source() -> None:
    rpc = _FakeRpc(chain_id=1)
    with pytest.raises(RuntimeError, match="chain id 56"):
        discover_fork_block(
            rpc,
            market="BNBUSD",
            lookback_blocks=20,
            confirmation_lag=4,
        )


def test_discovery_fails_closed_without_confirmed_bettable_state() -> None:
    rpc = _FakeRpc(
        timestamps={106: 1_300, 105: 1_301, 104: 1_302},
        rounds={
            106: (700, 1_000, 1_300),
            105: (700, 1_000, 1_300),
            104: (700, 1_000, 1_300),
        },
    )
    with pytest.raises(RuntimeError, match="no confirmed bettable Prediction state"):
        discover_fork_block(
            rpc,
            market="BNBUSD",
            lookback_blocks=3,
            confirmation_lag=4,
        )
