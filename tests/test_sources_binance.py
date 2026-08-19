from __future__ import annotations

import pytest

from pancake_prediction_ai.sources.binance import normalize_agg_trade, normalize_book_ticker


def test_agg_trade_preserves_exchange_and_local_times_separately() -> None:
    message = {
        "e": "aggTrade",
        "E": 1672515782136,
        "s": "BNBUSDT",
        "a": 12345,
        "p": "300.125",
        "q": "2.5",
        "f": 100,
        "l": 105,
        "T": 1672515782000,
        "m": True,
        "M": True,
    }
    observed_at_ns = 1_672_515_782_500_000_000
    event = normalize_agg_trade(message, observed_at_ns=observed_at_ns, expected_symbol="BNBUSDT")
    assert event.source == "binance_spot"
    assert event.topic == "market.agg_trade"
    assert event.event_time_ns == 1_672_515_782_000_000_000
    assert event.observed_at_ns == observed_at_ns
    assert event.payload["exchange_event_time_ms"] == 1672515782136
    assert event.payload["price"] == pytest.approx(300.125)
    assert event.payload["buyer_is_maker"] is True


def test_combined_stream_envelope_is_supported() -> None:
    message = {
        "stream": "bnbusdt@aggTrade",
        "data": {
            "e": "aggTrade",
            "E": 10,
            "s": "BNBUSDT",
            "a": 1,
            "p": "300",
            "q": "1",
            "f": 2,
            "l": 2,
            "T": 9,
            "m": False,
        },
    }
    event = normalize_agg_trade(message, observed_at_ns=11_000_000)
    assert event.event_id.endswith(":BNBUSDT:1")


def test_book_ticker_does_not_invent_exchange_timestamp() -> None:
    message = {
        "u": 400900217,
        "s": "BNBUSDT",
        "b": "25.35190000",
        "B": "31.21000000",
        "a": "25.36520000",
        "A": "40.66000000",
    }
    observed_at_ns = 123456789
    event = normalize_book_ticker(message, observed_at_ns=observed_at_ns)
    assert event.event_time_ns == observed_at_ns
    assert event.observed_at_ns == observed_at_ns
    assert event.payload["source_timestamp_available"] is False
    assert event.payload["spread"] > 0


def test_crossed_book_ticker_is_rejected() -> None:
    with pytest.raises(ValueError, match="bid cannot exceed"):
        normalize_book_ticker(
            {"u": 1, "s": "BNBUSDT", "b": "301", "B": "1", "a": "300", "A": "1"},
            observed_at_ns=1,
        )


def test_unexpected_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="unexpected Binance symbol"):
        normalize_book_ticker(
            {"u": 1, "s": "BTCUSDT", "b": "300", "B": "1", "a": "301", "A": "1"},
            observed_at_ns=1,
            expected_symbol="BNBUSDT",
        )
