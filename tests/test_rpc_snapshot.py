from __future__ import annotations

import json
from urllib.request import Request

import pytest

from pancake_prediction_ai.read_only_rpc import ReadOnlyJsonRpcClient, RpcError
from pancake_prediction_ai.rpc_snapshot import eth_call_at, fetch_block_anchor, get_code_at


ADDRESS = "0x1111111111111111111111111111111111111111"


def test_block_anchor_and_view_calls_use_concrete_block_tag() -> None:
    calls: list[dict[str, object]] = []

    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        calls.append(payload)
        method = payload["method"]
        if method == "eth_blockNumber":
            result: object = "0x64"
        elif method == "eth_getBlockByNumber":
            assert payload["params"] == ["0x64", False]
            result = {"number": "0x64", "hash": "0x" + "ab" * 32, "timestamp": "0x3e8"}
        elif method == "eth_call":
            assert payload["params"][1] == "0x64"
            result = "0x1234"
        elif method == "eth_getCode":
            assert payload["params"] == [ADDRESS, "0x64"]
            result = "0x6000"
        else:
            raise AssertionError(method)
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()

    client = ReadOnlyJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    anchor = fetch_block_anchor(client)
    assert anchor.number == 100
    assert anchor.timestamp_s == 1000
    assert anchor.rpc_tag == "0x64"
    assert eth_call_at(client, to=ADDRESS, data="0xabcdef01", anchor=anchor) == "0x1234"
    assert get_code_at(client, address=ADDRESS, anchor=anchor) == "0x6000"
    assert all("latest" not in json.dumps(call.get("params", [])) for call in calls)


def test_block_anchor_rejects_inconsistent_node_response() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        if payload["method"] == "eth_blockNumber":
            result: object = "0x64"
        else:
            result = {"number": "0x65", "hash": "0x" + "ab" * 32, "timestamp": "0x3e8"}
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode()

    client = ReadOnlyJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(RpcError, match="block number changed"):
        fetch_block_anchor(client)
