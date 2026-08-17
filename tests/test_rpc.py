import json
import urllib.error
import urllib.request

import pytest

from pancake_prediction.rpc import JsonRpcClient, LocalForkRpcClient, RpcResponseError


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self) -> bytes:
        return self._body


def test_json_rpc_application_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*args: object, **kwargs: object) -> _Response:
        nonlocal calls
        del args, kwargs
        calls += 1
        return _Response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": -32005,
                    "message": "query returned more than the provider limit",
                },
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = JsonRpcClient("http://127.0.0.1:8545", retries=4, backoff_s=0.0)
    with pytest.raises(RpcResponseError, match="-32005") as exc_info:
        client.get_logs("0x" + "11" * 20, 1, 10_000)
    assert exc_info.value.code == -32005
    assert calls == 1


def test_transport_failure_is_retried_and_can_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*args: object, **kwargs: object) -> _Response:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("temporary transport failure")
        return _Response({"jsonrpc": "2.0", "id": 2, "result": "0x38"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = JsonRpcClient("http://127.0.0.1:8545", retries=2, backoff_s=0.0)
    assert client.chain_id() == 56
    assert calls == 2


def test_rpc_retries_must_be_positive() -> None:
    client = JsonRpcClient("http://127.0.0.1:8545", retries=0)
    with pytest.raises(ValueError, match="retries must be positive"):
        client.chain_id()


def test_local_fork_rpc_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalForkRpcClient("https://bsc-dataseed.binance.org")


def test_local_fork_rpc_accepts_loopback_and_is_marked_fork_only() -> None:
    client = LocalForkRpcClient("http://127.0.0.1:8545")
    assert client.fork_only is True


def test_local_fork_transaction_lookup_uses_eth_get_transaction_by_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_payload: dict[str, object] = {}

    def fake_urlopen(request: urllib.request.Request, **kwargs: object) -> _Response:
        del kwargs
        raw = request.data
        assert isinstance(raw, bytes)
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        seen_payload.update(payload)
        return _Response(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"hash": "0x" + "aa" * 32},
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = LocalForkRpcClient("http://localhost:8545")
    tx_hash = "0x" + "aa" * 32
    result = client.transaction_by_hash(tx_hash)
    assert result is not None and result["hash"] == tx_hash
    assert seen_payload["method"] == "eth_getTransactionByHash"
    assert seen_payload["params"] == [tx_hash]
