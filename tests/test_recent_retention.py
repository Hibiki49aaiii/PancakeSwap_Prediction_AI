from __future__ import annotations

from typing import Any, cast

import pytest

from pancake_prediction.recent_bootstrap import (
    RecentBootstrapRpc,
    RecentSourceRetentionError,
    resolve_timestamp_block_range,
)
from pancake_prediction.rpc import RpcError


class PrunedHeaderRpc:
    def __init__(
        self,
        *,
        first_available: int,
        unrelated_error_block: int | None = None,
    ) -> None:
        self.first_available = first_available
        self.unrelated_error_block = unrelated_error_block
        self.requested_blocks: list[int] = []

    def block_number(self) -> int:
        return 1_023

    def block(self, number: int) -> dict[str, Any]:
        self.requested_blocks.append(number)
        if number == self.unrelated_error_block:
            raise RpcError("HTTP 403 Forbidden")
        if number < self.first_available:
            raise RpcError(f"block not found: {number}")
        return {
            "number": hex(number),
            "timestamp": hex(number),
            "hash": f"0x{number:064x}",
        }


def test_recent_range_recovers_from_overshoot_into_pruned_headers() -> None:
    header_rpc = PrunedHeaderRpc(first_available=995)
    rpc = cast(RecentBootstrapRpc, header_rpc)

    result = resolve_timestamp_block_range(
        rpc,
        start_timestamp=1_000,
        end_timestamp=1_010,
        confirmations=0,
    )

    assert result.from_block == 1_000
    assert result.to_block == 1_009
    assert 991 in header_rpc.requested_blocks
    assert 995 in header_rpc.requested_blocks


def test_recent_range_fails_closed_when_retention_starts_after_target() -> None:
    rpc = cast(RecentBootstrapRpc, PrunedHeaderRpc(first_available=1_005))

    with pytest.raises(RecentSourceRetentionError) as exc_info:
        resolve_timestamp_block_range(
            rpc,
            start_timestamp=1_000,
            end_timestamp=1_010,
            confirmations=0,
        )

    assert exc_info.value.as_dict() == {
        "classification": "PROVIDER_RETENTION",
        "requested_timestamp": 1_000,
        "first_available_block": 1_005,
        "first_available_timestamp": 1_005,
        "last_unavailable_block": 1_004,
    }


def test_recent_range_does_not_reclassify_unrelated_rpc_errors_as_retention() -> None:
    rpc = cast(
        RecentBootstrapRpc,
        PrunedHeaderRpc(first_available=0, unrelated_error_block=991),
    )

    with pytest.raises(RpcError, match="HTTP 403 Forbidden"):
        resolve_timestamp_block_range(
            rpc,
            start_timestamp=1_000,
            end_timestamp=1_010,
            confirmations=0,
        )
