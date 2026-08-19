from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.local_fork_rpc import LocalForkJsonRpcClient, LocalForkRpcError


def test_local_fork_client_allows_only_inspection_mine_and_reset_methods() -> None:
    calls: list[str] = []

    def transport(request, timeout: float) -> bytes:
        assert timeout == 2.0
        body = json.loads(request.data)
        calls.append(body["method"])
        result: object = True
        if body["method"] == "eth_chainId":
            result = "0x38"
        return json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()

    client = LocalForkJsonRpcClient(
        "http://127.0.0.1:8545",
        timeout_seconds=2.0,
        transport=transport,
    )
    assert client.call("eth_chainId", []) == "0x38"
    assert client.call("evm_mine", []) is True
    assert client.call("anvil_reset", []) is True
    assert calls == ["eth_chainId", "evm_mine", "anvil_reset"]


@pytest.mark.parametrize(
    "method",
    [
        "eth_sendTransaction",
        "eth_sendRawTransaction",
        "eth_sign",
        "personal_sign",
        "eth_signTransaction",
        "personal_unlockAccount",
    ],
)
def test_transaction_and_signing_methods_are_rejected_before_transport(method: str) -> None:
    transported = False

    def transport(request, timeout: float) -> bytes:
        nonlocal transported
        transported = True
        raise AssertionError("forbidden method reached transport")

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(PermissionError, match="outside local-fork boundary"):
        client.call(method, [])
    assert not transported


def test_local_fork_client_validates_endpoint_and_timeout() -> None:
    with pytest.raises(ValueError, match="http"):
        LocalForkJsonRpcClient("not-a-url")
    with pytest.raises(ValueError, match="positive"):
        LocalForkJsonRpcClient("http://127.0.0.1:8545", timeout_seconds=0)


def test_local_fork_client_rejects_invalid_rpc_response_id() -> None:
    def transport(request, timeout: float) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": 999, "result": True}).encode()

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(LocalForkRpcError, match="id mismatch"):
        client.call("evm_mine", [])
