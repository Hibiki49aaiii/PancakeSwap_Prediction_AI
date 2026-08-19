from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from pancake_prediction_ai.binance_public_rest import BinancePublicDataError, BinancePublicRestClient


def test_collect_book_ticker_uses_response_arrival_as_observation_time() -> None:
    response_arrived = False

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal response_arrived
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "data-api.binance.vision"
        assert parsed.path == "/api/v3/ticker/bookTicker"
        query = parse_qs(parsed.query)
        assert query["symbol"] == ["BNBUSDT"]
        assert query["symbolStatus"] == ["TRADING"]
        response_arrived = True
        return json.dumps(
            {
                "symbol": "BNBUSDT",
                "bidPrice": "599.0",
                "bidQty": "10",
                "askPrice": "601.0",
                "askQty": "11",
            }
        ).encode()

    def clock_ns() -> int:
        assert response_arrived
        return 123_456_789

    client = BinancePublicRestClient(http_get=http_get, clock_ns=clock_ns)
    event = client.collect_book_ticker("bnbusdt")
    assert event.observed_at_ns == 123_456_789
    assert event.event_time_ns == 123_456_789
    assert event.payload["capture_transport"] == "rest"
    assert event.payload["sequence_id_available"] is False
    assert event.payload["update_id"] is None


def test_rest_backfill_trade_keeps_old_trade_time_but_current_observation_time() -> None:
    def http_get(url: str, timeout: float) -> bytes:
        parsed = urlparse(url)
        assert parsed.path == "/api/v3/aggTrades"
        query = parse_qs(parsed.query)
        assert query["symbol"] == ["BNBUSDT"]
        assert query["fromId"] == ["100"]
        assert query["limit"] == ["2"]
        return json.dumps(
            [
                {"a": 100, "p": "600", "q": "1", "f": 1000, "l": 1000, "T": 1_000, "m": False, "M": True},
                {"a": 101, "p": "601", "q": "2", "f": 1001, "l": 1002, "T": 2_000, "m": True, "M": True},
            ]
        ).encode()

    client = BinancePublicRestClient(http_get=http_get, clock_ns=lambda: 999_000_000_000)
    events = client.collect_aggregate_trades("BNBUSDT", from_id=100, limit=2)
    assert len(events) == 2
    assert events[0].event_time_ns == 1_000_000_000
    assert events[1].event_time_ns == 2_000_000_000
    assert all(event.observed_at_ns == 999_000_000_000 for event in events)
    assert all(event.payload["capture_transport"] == "rest" for event in events)
    assert events[0].payload["exchange_event_time_ms"] is None


def test_server_time_and_binance_error_handling() -> None:
    calls = 0

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            return b'{"serverTime":1700000000000}'
        return b'{"code":-1121,"msg":"Invalid symbol."}'

    client = BinancePublicRestClient(http_get=http_get)
    assert client.server_time_ms() == 1_700_000_000_000
    with pytest.raises(BinancePublicDataError, match="Invalid symbol"):
        client.book_ticker("NOPEUSDT")


def test_aggregate_trade_parameter_bounds_are_enforced_before_http() -> None:
    called = False

    def http_get(url: str, timeout: float) -> bytes:
        nonlocal called
        called = True
        return b"[]"

    client = BinancePublicRestClient(http_get=http_get)
    with pytest.raises(ValueError, match="limit"):
        client.aggregate_trades("BNBUSDT", limit=1001)
    with pytest.raises(ValueError, match="start_time_ms"):
        client.aggregate_trades("BNBUSDT", start_time_ms=2, end_time_ms=1)
    assert not called
