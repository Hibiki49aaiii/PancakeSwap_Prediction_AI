from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.local_fork_rpc import LocalForkJsonRpcClient, LocalForkRpcError


def test_local_fork_client_guards_mutations_with_exact_snapshot_restore() -> None:
    calls: list[tuple[str, list[object]]] = []
    snapshot_counter = 0

    def transport(request, timeout: float) -> bytes:
        nonlocal snapshot_counter
        assert timeout == 2.0
        body = json.loads(request.data)
        method = body["method"]
        params = body["params"]
        calls.append((method, params))
        result: object = True
        if method == "eth_chainId":
            result = "0x38"
        elif method == "evm_snapshot":
            result = hex(snapshot_counter)
            snapshot_counter += 1
        elif method == "eth_sendTransaction":
            result = "0x" + "ab" * 32
        return json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()

    client = LocalForkJsonRpcClient(
        "http://127.0.0.1:8545",
        timeout_seconds=2.0,
        transport=transport,
    )
    assert client.call("eth_chainId", []) == "0x38"
    assert client.call("eth_sendTransaction", [{"from": "0x1", "to": "0x2"}]) == "0x" + "ab" * 32
    assert client.call("anvil_impersonateAccount", ["0x1"]) is True
    assert client.call("anvil_setBalance", ["0x1", "0x1"]) is True
    assert client.call("evm_mine", []) is True
    assert client.call("anvil_reset", []) is True
    assert calls == [
        ("eth_chainId", []),
        ("evm_snapshot", []),
        ("eth_sendTransaction", [{"from": "0x1", "to": "0x2"}]),
        ("anvil_impersonateAccount", ["0x1"]),
        ("anvil_setBalance", ["0x1", "0x1"]),
        ("evm_mine", []),
        ("evm_revert", ["0x0"]),
    ]


def test_new_mutation_cycle_takes_a_fresh_snapshot_after_restore() -> None:
    calls: list[tuple[str, list[object]]] = []
    snapshots = iter(["0x11", "0x12"])

    def transport(request, timeout: float) -> bytes:
        body = json.loads(request.data)
        method = body["method"]
        params = body["params"]
        calls.append((method, params))
        result: object = True
        if method == "evm_snapshot":
            result = next(snapshots)
        return json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    assert client.call("evm_mine", []) is True
    assert client.call("anvil_reset", []) is True
    assert client.call("anvil_setBalance", ["0x1", "0x2"]) is True
    assert client.call("anvil_reset", []) is True
    assert calls == [
        ("evm_snapshot", []),
        ("evm_mine", []),
        ("evm_revert", ["0x11"]),
        ("evm_snapshot", []),
        ("anvil_setBalance", ["0x1", "0x2"]),
        ("evm_revert", ["0x12"]),
    ]


def test_reset_without_guarded_mutation_is_rejected_before_transport() -> None:
    calls: list[str] = []

    def transport(request, timeout: float) -> bytes:
        calls.append(json.loads(request.data)["method"])
        raise AssertionError("reset without snapshot must not reach transport")

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(LocalForkRpcError, match="without an active snapshot"):
        client.call("anvil_reset", [])
    assert calls == []


@pytest.mark.parametrize(
    "method",
    [
        "eth_sendRawTransaction",
        "eth_sign",
        "personal_sign",
        "eth_signTransaction",
        "personal_unlockAccount",
        "wallet_addEthereumChain",
        "evm_snapshot",
        "evm_revert",
    ],
)
def test_raw_transaction_signing_and_internal_snapshot_methods_are_rejected_before_transport(
    method: str,
) -> None:
    transported = False

    def transport(request, timeout: float) -> bytes:
        nonlocal transported
        transported = True
        raise AssertionError("forbidden method reached transport")

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(PermissionError, match="outside local-fork boundary"):
        client.call(method, [])
    assert not transported


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.168.1.10:8545",
        "https://bsc-dataseed.binance.org",
        "http://example.com:8545",
    ],
)
def test_non_loopback_endpoint_is_rejected_before_any_rpc(endpoint: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalForkJsonRpcClient(endpoint)


def test_loopback_endpoint_with_credentials_is_rejected() -> None:
    with pytest.raises(ValueError, match="credentials"):
        LocalForkJsonRpcClient("http://user:pass@127.0.0.1:8545")


def test_localhost_and_ipv6_loopback_are_accepted() -> None:
    LocalForkJsonRpcClient("http://localhost:8545")
    LocalForkJsonRpcClient("http://[::1]:8545")


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


def test_local_fork_rpc_error_preserves_revert_data_for_diagnostics() -> None:
    def transport(request, timeout: float) -> bytes:
        body = json.loads(request.data)
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {"code": 3, "message": "execution reverted", "data": "0xdeadbeef"},
            }
        ).encode()

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(LocalForkRpcError, match="data=0xdeadbeef"):
        client.call("eth_call", [{}, "latest"])


def test_failed_revert_clears_stale_snapshot() -> None:
    calls: list[str] = []

    def transport(request, timeout: float) -> bytes:
        body = json.loads(request.data)
        method = body["method"]
        calls.append(method)
        result: object = "0x7" if method == "evm_snapshot" else False if method == "evm_revert" else True
        return json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()

    client = LocalForkJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    assert client.call("evm_mine", []) is True
    with pytest.raises(LocalForkRpcError, match="did not restore"):
        client.call("anvil_reset", [])
    # The failed snapshot is not silently reused; the next mutation creates a new one.
    assert client.call("evm_mine", []) is True
    assert calls == ["evm_snapshot", "evm_mine", "evm_revert", "evm_snapshot", "evm_mine"]
