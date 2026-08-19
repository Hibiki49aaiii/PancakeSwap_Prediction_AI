from __future__ import annotations

import json
from urllib.request import Request

import pytest

from pancake_prediction_ai.read_only_rpc import ReadOnlyJsonRpcClient, RpcError


def test_read_only_rpc_parses_chain_id() -> None:
    seen: list[dict[str, object]] = []

    def transport(request: Request, timeout: float) -> bytes:
        assert timeout == 3.0
        payload = json.loads(request.data or b"{}")
        seen.append(payload)
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": "0x38"}).encode()

    client = ReadOnlyJsonRpcClient("http://127.0.0.1:8545", timeout_seconds=3.0, transport=transport)
    assert client.chain_id() == 56
    assert seen[0]["method"] == "eth_chainId"


@pytest.mark.parametrize(
    "method",
    [
        "eth_sendRawTransaction",
        "eth_sendTransaction",
        "personal_sendTransaction",
        "wallet_sendCalls",
        "anvil_setBalance",
    ],
)
def test_write_or_mutating_rpc_methods_are_rejected_before_transport(method: str) -> None:
    called = False

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal called
        called = True
        return b"{}"

    client = ReadOnlyJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(PermissionError, match="outside read-only boundary"):
        client.call(method, [])
    assert not called


def test_rpc_error_is_not_silently_converted_to_result() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": -32000, "message": "node unavailable"},
            }
        ).encode()

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    with pytest.raises(RpcError, match="node unavailable"):
        client.block_number()


def test_response_id_mismatch_is_rejected() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        return b'{"jsonrpc":"2.0","id":999,"result":"0x38"}'

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    with pytest.raises(RpcError, match="id mismatch"):
        client.chain_id()
