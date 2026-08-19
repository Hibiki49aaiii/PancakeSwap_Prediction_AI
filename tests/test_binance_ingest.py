from __future__ import annotations

import pytest

from pancake_prediction_ai.binance_ingest import (
    BinanceDataGapError,
    latest_aggregate_trade_id,
    poll_binance_public_once,
)
from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade, normalize_rest_book_ticker


def _trade(trade_id: int, *, observed_at_ns: int):
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
        observed_at_ns=observed_at_ns,
    )


class FakeClient:
    def __init__(self, batches: list[tuple[int, ...]]) -> None:
        self.batches = batches
        self.requests: list[int | None] = []
        self.book_calls = 0
        self.batch_index = 0

    def collect_aggregate_trades(self, symbol: str, *, from_id=None, start_time_ms=None, end_time_ms=None, limit=500):
        self.requests.append(from_id)
        ids = self.batches[self.batch_index]
        self.batch_index += 1
        return tuple(_trade(value, observed_at_ns=1_000_000 + self.batch_index) for value in ids)

    def collect_book_ticker(self, symbol: str):
        self.book_calls += 1
        return normalize_rest_book_ticker(
            {
                "symbol": symbol,
                "bidPrice": "599",
                "bidQty": "1",
                "askPrice": "601",
                "askQty": "1",
            },
            observed_at_ns=10_000_000 + self.book_calls,
        )


def test_ingestor_recovers_last_trade_id_from_store_after_restart(tmp_path) -> None:
    client = FakeClient([(10, 11), (12,)])
    with EventStore(tmp_path / "events.sqlite") as store:
        first = poll_binance_public_once(client, store, symbol="BNBUSDT", trade_limit=1000)
        assert first.trades_appended == 2
        assert first.last_aggregate_trade_id == 11
        assert latest_aggregate_trade_id(store, symbol="BNBUSDT") == 11

        second = poll_binance_public_once(client, store, symbol="BNBUSDT", trade_limit=1000)
        assert second.trades_appended == 1
        assert second.last_aggregate_trade_id == 12
        assert latest_aggregate_trade_id(store, symbol="BNBUSDT") == 12

        events = store.read_all_ingest_order()
        assert sum(item.event.topic == "market.agg_trade" for item in events) == 3
        assert sum(item.event.topic == "market.book_ticker" for item in events) == 2

    assert client.requests == [None, 12]
    assert client.book_calls == 2


def test_gap_aborts_before_suspect_batch_or_book_is_appended(tmp_path) -> None:
    client = FakeClient([(13,)])
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_trade(11, observed_at_ns=1))
        with pytest.raises(BinanceDataGapError, match="expected 12, received 13"):
            poll_binance_public_once(client, store, symbol="BNBUSDT")
        assert latest_aggregate_trade_id(store, symbol="BNBUSDT") == 11
        assert len(store.read_all_ingest_order()) == 1
    assert client.requests == [12]
    assert client.book_calls == 0
