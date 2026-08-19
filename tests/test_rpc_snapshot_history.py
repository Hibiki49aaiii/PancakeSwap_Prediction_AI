from __future__ import annotations

import pytest

from pancake_prediction_ai.rpc_snapshot import (
    fetch_block_anchor_by_number,
    find_block_at_or_before_timestamp,
)


class FakeRpc:
    def call(self, method: str, params: list[object]):
        assert method == "eth_getBlockByNumber"
        number = int(str(params[0]), 16)
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "timestamp": hex(1_000 + number * 3),
        }


def test_fetch_explicit_historical_block_anchor() -> None:
    anchor = fetch_block_anchor_by_number(FakeRpc(), 10)  # type: ignore[arg-type]
    assert anchor.number == 10
    assert anchor.timestamp_s == 1030
    assert anchor.rpc_tag == "0xa"


def test_find_latest_block_not_after_target_timestamp() -> None:
    anchor = find_block_at_or_before_timestamp(
        FakeRpc(),  # type: ignore[arg-type]
        target_timestamp_s=1040,
        lower_block=0,
        upper_block=20,
    )
    # block 13 -> 1039, block 14 -> 1042
    assert anchor.number == 13
    assert anchor.timestamp_s == 1039


def test_target_before_lower_bound_is_rejected() -> None:
    with pytest.raises(ValueError, match="predates lower block"):
        find_block_at_or_before_timestamp(
            FakeRpc(),  # type: ignore[arg-type]
            target_timestamp_s=1001,
            lower_block=1,
            upper_block=20,
        )
