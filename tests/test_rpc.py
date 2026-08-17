import json
import urllib.error
import urllib.request

import pytest

from pancake_prediction.rpc import JsonRpcClient, RpcResponseError


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
