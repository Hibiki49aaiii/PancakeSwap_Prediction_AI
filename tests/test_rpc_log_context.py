from __future__ import annotations

from typing import Any

import pytest

from pancake_prediction.rpc import JsonRpcClient, RpcLogQueryError, RpcResponseError


class FailingLogsRpc(JsonRpcClient):
    def call(self, method: str, params: list[object]) -> Any:
        assert method == "eth_getLogs"
        raise RpcResponseError(method, {"code": -32005, "message": "limit exceeded"})


def test_get_logs_preserves_single_block_provider_limit_context() -> None:
    rpc = FailingLogsRpc("https://example.invalid")
    topic = "0x" + "12" * 32

    with pytest.raises(RpcLogQueryError) as exc_info:
        rpc.get_logs(
            "0x" + "34" * 20,
            116172651,
            116172651,
            topic0s=(topic,),
        )

    assert "-32005" in str(exc_info.value)
    assert exc_info.value.as_dict() == {
        "classification": "PROVIDER_LOG_LIMIT",
        "rpc_code": -32005,
        "address": "0x" + "34" * 20,
        "from_block": 116172651,
        "to_block": 116172651,
        "range_blocks": 1,
        "topic0_count": 1,
        "topic0s": [topic],
        "single_block_reached": True,
        "provider_message": "eth_getLogs: JSON-RPC error code=-32005: limit exceeded",
    }


def test_get_logs_keeps_multiblock_limit_split_eligible() -> None:
    rpc = FailingLogsRpc("https://example.invalid")

    with pytest.raises(RpcLogQueryError) as exc_info:
        rpc.get_logs("0x" + "56" * 20, 100, 199, topic0s=None)

    details = exc_info.value.as_dict()
    assert details["classification"] == "RPC_LOG_ERROR"
    assert details["range_blocks"] == 100
    assert details["single_block_reached"] is False
    assert "-32005" in str(exc_info.value)
