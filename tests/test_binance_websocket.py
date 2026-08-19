from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.binance_ingest import BinanceDataGapError
from pancake_prediction_ai.binance_websocket import (
    BinanceMarketWebSocketIngestor,
    catch_up_aggregate_trades,
    run_reconnecting_market_stream,
)
from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade


def _trade_message(trade_id: int) -> str:
    return json.dumps(
        {
            "stream": "bnbusdt@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": trade_id * 1000 + 1,
                "s": "BNBUSDT",
                "a": trade_id,
                "p": "600",
                "q": "1",
                "f": trade_id,
                "l": trade_id,
                "T": trade_id * 1000,
                "m": False,
            },
        }
    )


def _book_message(update_id: int) -> str:
    return json.dumps(
        {
            "stream": "bnbusdt@bookTicker",
            "data": {
                "u": update_id,
                "s": "BNBUSDT",
                "b": "599",
                "B": "1",
                "a": "601",
                "A": "1",
            },
        }
    )


def _rest_trade(trade_id: int):
    return normalize_rest_agg_trade(
        {
            "a": trade_id,
            "p": "600",
            "q": "1",
            "f": trade_id,
            "l": trade_id,
            "T": trade_id * 1000,
            "m": False,
        },
        symbol="BNBUSDT",
        observed_at_ns=999_000_000_000,
    )


def test_websocket_ingestor_deduplicates_and_detects_forward_trade_gap(tmp_path) -> None:
    clock = iter([1_000, 2_000, 3_000, 4_000, 5_000]).__next__
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_rest_trade(10))
        ingestor = BinanceMarketWebSocketIngestor(store, clock_ns=clock)
        assert ingestor.last_trade_id == 10
        assert ingestor.process_message(_trade_message(10)) == "duplicate_or_stale_trade"
        assert ingestor.process_message(_trade_message(11)) == "agg_trade"
        assert ingestor.last_trade_id == 11
        with pytest.raises(BinanceDataGapError, match="expected 12, received 13"):
            ingestor.process_message(_trade_message(13))
        assert ingestor.last_trade_id == 11
        assert ingestor.stats.duplicate_or_stale_messages == 1
        assert ingestor.stats.agg_trades_appended == 1


def test_book_update_id_need_only_be_monotonic_not_contiguous(tmp_path) -> None:
    clock = iter([1_000, 2_000, 3_000]).__next__
    with EventStore(tmp_path / "events.sqlite") as store:
        ingestor = BinanceMarketWebSocketIngestor(store, clock_ns=clock)
        assert ingestor.process_message(_book_message(100)) == "book_ticker"
        assert ingestor.process_message(_book_message(105)) == "book_ticker"
        assert ingestor.process_message(_book_message(104)) == "duplicate_or_stale_book"
        assert ingestor.stats.book_tickers_appended == 2
        assert ingestor.stats.duplicate_or_stale_messages == 1


def test_run_connection_uses_market_data_only_combined_stream_and_runtime_ping_settings(tmp_path) -> None:
    seen: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def __iter__(self):
            return iter([_trade_message(1), _book_message(10)])

    def connect_fn(uri: str, **kwargs):
        seen["uri"] = uri
        seen.update(kwargs)
        return FakeConnection()

    clock = iter([1_000, 2_000]).__next__
    with EventStore(tmp_path / "events.sqlite") as store:
        ingestor = BinanceMarketWebSocketIngestor(store, clock_ns=clock)
        stats = ingestor.run_connection(connect_fn=connect_fn)
        assert stats.agg_trades_appended == 1
        assert stats.book_tickers_appended == 1

    assert seen["uri"] == "wss://data-stream.binance.vision:443/stream?streams=bnbusdt@aggTrade/bnbusdt@bookTicker"
    assert seen["ping_interval"] == 20
    assert seen["ping_timeout"] == 20


def test_rest_catchup_repairs_exact_trade_gap(tmp_path) -> None:
    class Rest:
        def __init__(self):
            self.calls: list[int | None] = []

        def collect_aggregate_trades(self, symbol: str, *, from_id=None, start_time_ms=None, end_time_ms=None, limit=500):
            self.calls.append(from_id)
            if from_id == 12:
                return (_rest_trade(12), _rest_trade(13))
            return ()

    rest = Rest()
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_rest_trade(11))
        ingestor = BinanceMarketWebSocketIngestor(store)
        appended = catch_up_aggregate_trades(ingestor, rest, limit=1000)
        assert appended == 2
        assert ingestor.last_trade_id == 13
        assert rest.calls == [12]


def test_rest_catchup_rejects_noncontiguous_batch_before_advancing(tmp_path) -> None:
    class Rest:
        def collect_aggregate_trades(self, symbol: str, *, from_id=None, start_time_ms=None, end_time_ms=None, limit=500):
            return (_rest_trade(13),)

    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_rest_trade(11))
        ingestor = BinanceMarketWebSocketIngestor(store)
        with pytest.raises(BinanceDataGapError, match="expected 12, received 13"):
            catch_up_aggregate_trades(ingestor, Rest())
        assert ingestor.last_trade_id == 11


def test_reconnecting_runner_repairs_gap_then_reconnects(tmp_path) -> None:
    connections = 0

    class FakeConnection:
        def __init__(self, messages: list[str]):
            self.messages = messages

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def __iter__(self):
            return iter(self.messages)

    def connect_fn(uri: str, **kwargs):
        nonlocal connections
        connections += 1
        if connections == 1:
            return FakeConnection([_trade_message(13)])
        return FakeConnection([_trade_message(14)])

    class Rest:
        def collect_aggregate_trades(self, symbol: str, *, from_id=None, start_time_ms=None, end_time_ms=None, limit=500):
            assert from_id == 12
            return (_rest_trade(12), _rest_trade(13))

    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_rest_trade(11))
        ingestor = BinanceMarketWebSocketIngestor(store, clock_ns=lambda: 1_000_000_000_000)
        stats = run_reconnecting_market_stream(
            ingestor,
            rest=Rest(),
            connect_fn=connect_fn,
            sleep=lambda _: None,
            reconnect_delay_seconds=0,
            max_reconnects=1,
        )
        assert ingestor.last_trade_id == 14
        assert stats.agg_trades_appended == 3
        assert connections == 2
