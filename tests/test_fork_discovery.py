from __future__ import annotations

from typing import Any

import pytest

from pancake_prediction.fork_discovery import discover_fork_block


class _FakeRpc:
    def __init__(
        self,
        *,
        chain_id: int = 56,
        head: int = 110,
        logs: list[dict[str, Any]] | None = None,
        timestamps: dict[int, int] | None = None,
    ) -> None:
        self._chain_id = chain_id
        self._head = head
        self._logs = logs or []
        self._timestamps = timestamps or {}

    def chain_id(self) -> int:
        return self._chain_id

    def block_number(self) -> int:
        return self._head

    def block(self, number: int) -> dict[str, Any]:
        return {"timestamp": hex(self._timestamps.get(number, number))}

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        del address, topic0s
        return [
            log
            for log in self._logs
            if from_block <= int(str(log["blockNumber"]), 16) <= to_block
        ]


def test_discovery_skips_same_timestamp_block_after_start_round() -> None:
    rpc = _FakeRpc(
        logs=[{"blockNumber": hex(100), "logIndex": "0x0"}],
        timestamps={100: 1_000, 101: 1_000, 102: 1_001},
    )
    fork_block, start_round_block = discover_fork_block(
        rpc,
        market="BNBUSD",
        lookback_blocks=50,
        confirmation_lag=4,
        chunk_size=50,
    )
    assert start_round_block == 100
    assert fork_block == 102


def test_discovery_falls_back_to_older_round_if_latest_is_not_ready() -> None:
    rpc = _FakeRpc(
        logs=[
            {"blockNumber": hex(105), "logIndex": "0x0"},
            {"blockNumber": hex(100), "logIndex": "0x0"},
        ],
        timestamps={
            100: 1_000,
            101: 1_001,
            105: 2_000,
            106: 2_000,
        },
    )
    fork_block, start_round_block = discover_fork_block(
        rpc,
        market="BNBUSD",
        lookback_blocks=50,
        confirmation_lag=4,
        chunk_size=50,
    )
    assert start_round_block == 100
    assert fork_block == 101


def test_discovery_rejects_non_bsc_source() -> None:
    rpc = _FakeRpc(chain_id=1)
    with pytest.raises(RuntimeError, match="chain id 56"):
        discover_fork_block(
            rpc,
            market="BNBUSD",
            lookback_blocks=50,
            confirmation_lag=4,
            chunk_size=50,
        )


def test_discovery_fails_closed_without_confirmed_usable_round() -> None:
    rpc = _FakeRpc(logs=[])
    with pytest.raises(RuntimeError, match="no confirmed StartRound"):
        discover_fork_block(
            rpc,
            market="BNBUSD",
            lookback_blocks=50,
            confirmation_lag=4,
            chunk_size=50,
        )
