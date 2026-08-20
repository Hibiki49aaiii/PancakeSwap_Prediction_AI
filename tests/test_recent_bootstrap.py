from __future__ import annotations

from typing import Any, cast

import pytest

from pancake_prediction.recent_bootstrap import (
    RecentBootstrapRpc,
    first_block_at_or_after,
    resolve_timestamp_block_range,
)


class HeaderRpc:
    def __init__(self, timestamps: tuple[int, ...]) -> None:
        self.timestamps = timestamps

    def block_number(self) -> int:
        return len(self.timestamps) - 1

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "timestamp": hex(self.timestamps[number]),
            "hash": f"0x{number:064x}",
        }


class RecordingHeaderRpc(HeaderRpc):
    def __init__(self, timestamps: tuple[int, ...]) -> None:
        super().__init__(timestamps)
        self.requested_blocks: list[int] = []

    def block(self, number: int) -> dict[str, Any]:
        self.requested_blocks.append(number)
        return super().block(number)


def _rpc() -> RecentBootstrapRpc:
    return cast(
        RecentBootstrapRpc,
        HeaderRpc((100, 110, 120, 130, 140, 150, 160, 170, 180, 190)),
    )


def test_first_block_at_or_after_uses_header_time_only() -> None:
    rpc = _rpc()
    assert first_block_at_or_after(rpc, 100, upper_block=9) == 0
    assert first_block_at_or_after(rpc, 121, upper_block=9) == 3
    assert first_block_at_or_after(rpc, 190, upper_block=9) == 9


def test_recent_range_uses_exclusive_end_and_confirmed_head() -> None:
    result = resolve_timestamp_block_range(
        _rpc(),
        start_timestamp=121,
        end_timestamp=171,
        confirmations=1,
    )

    assert result.from_block == 3
    assert result.to_block == 7
    assert result.from_block_timestamp == 130
    assert result.to_block_timestamp == 170
    assert result.head_block == 9
    assert result.confirmations == 1


def test_recent_range_search_stays_near_recent_window() -> None:
    header_rpc = RecordingHeaderRpc(tuple(range(1_024)))
    rpc = cast(RecentBootstrapRpc, header_rpc)

    result = resolve_timestamp_block_range(
        rpc,
        start_timestamp=1_000,
        end_timestamp=1_010,
        confirmations=0,
    )

    assert result.from_block == 1_000
    assert result.to_block == 1_009
    assert min(header_rpc.requested_blocks) >= 991
    assert 0 not in header_rpc.requested_blocks


def test_recent_range_rejects_future_or_reversed_window() -> None:
    rpc = _rpc()
    with pytest.raises(ValueError, match="greater"):
        resolve_timestamp_block_range(
            rpc,
            start_timestamp=170,
            end_timestamp=160,
            confirmations=1,
        )
    with pytest.raises(ValueError, match="confirmed head"):
        resolve_timestamp_block_range(
            rpc,
            start_timestamp=150,
            end_timestamp=181,
            confirmations=1,
        )
