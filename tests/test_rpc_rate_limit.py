from __future__ import annotations

import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from pancake_prediction.rpc import JsonRpcClient


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self) -> bytes:
        return self._body


def test_json_rpc_client_enforces_minimum_request_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_values = iter((100.0, 100.02))
    sleeps: list[float] = []

    monkeypatch.setattr(
        "pancake_prediction.rpc.time.monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr("pancake_prediction.rpc.time.sleep", sleeps.append)

    def fake_urlopen(request: urllib.request.Request, **kwargs: object) -> _Response:
        del kwargs
        raw = request.data
        assert isinstance(raw, bytes)
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        return _Response(
            {"jsonrpc": "2.0", "id": payload["id"], "result": "0x38"}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = JsonRpcClient(
        "https://example.invalid",
        retries=1,
        min_interval_s=0.1,
    )

    assert client.chain_id() == 56
    assert client.chain_id() == 56
    assert sleeps == pytest.approx([0.08])


def test_json_rpc_client_honors_retry_after_on_http_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []
    headers = Message()
    headers["Retry-After"] = "2"

    def fake_urlopen(request: urllib.request.Request, **kwargs: object) -> _Response:
        nonlocal calls
        del kwargs
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                headers,
                None,
            )
        raw = request.data
        assert isinstance(raw, bytes)
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        return _Response(
            {"jsonrpc": "2.0", "id": payload["id"], "result": "0x38"}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("pancake_prediction.rpc.time.sleep", sleeps.append)
    client = JsonRpcClient(
        "https://example.invalid",
        retries=2,
        backoff_s=0.25,
    )

    assert client.chain_id() == 56
    assert calls == 2
    assert sleeps == [2.0]


def test_json_rpc_client_uses_exponential_backoff_without_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_urlopen(request: urllib.request.Request, **kwargs: object) -> _Response:
        nonlocal calls
        del kwargs
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                Message(),
                None,
            )
        raw = request.data
        assert isinstance(raw, bytes)
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        return _Response(
            {"jsonrpc": "2.0", "id": payload["id"], "result": "0x38"}
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("pancake_prediction.rpc.time.sleep", sleeps.append)
    client = JsonRpcClient(
        "https://example.invalid",
        retries=2,
        backoff_s=0.75,
    )

    assert client.chain_id() == 56
    assert calls == 2
    assert sleeps == [0.75]


def test_json_rpc_client_rejects_negative_rate_limit_settings() -> None:
    with pytest.raises(ValueError, match="min_interval_s"):
        JsonRpcClient("https://example.invalid", min_interval_s=-0.1).chain_id()
    with pytest.raises(ValueError, match="backoff_s"):
        JsonRpcClient("https://example.invalid", backoff_s=-0.1).chain_id()
