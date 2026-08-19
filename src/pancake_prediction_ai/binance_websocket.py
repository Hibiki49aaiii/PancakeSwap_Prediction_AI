from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .binance_ingest import BinanceDataGapError, latest_aggregate_trade_id
from .binance_public_rest import BinancePublicRestClient
from .event_store import EventStore
from .sources.binance import normalize_agg_trade, normalize_book_ticker


MARKET_DATA_WS_BASE_URL = "wss://data-stream.binance.vision:443"
ClockNs = Callable[[], int]
Sleep = Callable[[float], None]


class WebSocketConnection(Protocol):
    def __iter__(self) -> Iterator[str | bytes]: ...
    def __enter__(self) -> "WebSocketConnection": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...


ConnectFn = Callable[..., WebSocketConnection]


@dataclass(frozen=True, slots=True)
class WebSocketIngestStats:
    agg_trades_appended: int = 0
    book_tickers_appended: int = 0
    duplicate_or_stale_messages: int = 0

    def add(self, *, trades: int = 0, books: int = 0, stale: int = 0) -> "WebSocketIngestStats":
        return WebSocketIngestStats(
            agg_trades_appended=self.agg_trades_appended + trades,
            book_tickers_appended=self.book_tickers_appended + books,
            duplicate_or_stale_messages=self.duplicate_or_stale_messages + stale,
        )


class BinanceMarketWebSocketIngestor:
    """Persist Binance public `aggTrade` and `bookTicker` streams.

    The market-data-only Binance domain cannot access user data. Aggregate
    trade IDs are treated as a continuity signal. Duplicate/stale messages are
    ignored; a forward ID gap raises `BinanceDataGapError` so the caller can
    repair it from public REST before reconnecting.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        symbol: str = "BNBUSDT",
        clock_ns: ClockNs = time.time_ns,
    ) -> None:
        if not symbol or not symbol.isascii():
            raise ValueError("symbol must be non-empty ASCII")
        self.store = store
        self.symbol = symbol.upper()
        self.clock_ns = clock_ns
        self._last_trade_id = latest_aggregate_trade_id(store, symbol=self.symbol)
        self._last_book_update_id = self._recover_last_book_update_id()
        self.stats = WebSocketIngestStats()

    @property
    def stream_uri(self) -> str:
        lower = self.symbol.lower()
        return f"{MARKET_DATA_WS_BASE_URL}/stream?streams={lower}@aggTrade/{lower}@bookTicker"

    @property
    def last_trade_id(self) -> int | None:
        return self._last_trade_id

    def _recover_last_book_update_id(self) -> int | None:
        for stored in reversed(self.store.read_all_ingest_order()):
            event = stored.event
            if event.source != "binance_spot" or event.topic != "market.book_ticker":
                continue
            if event.payload.get("symbol") != self.symbol:
                continue
            value = event.payload.get("update_id")
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("stored Binance book update_id is invalid")
            return value
        return None

    @staticmethod
    def _decode_message(message: str | bytes) -> dict[str, Any]:
        if isinstance(message, bytes):
            try:
                message = message.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Binance WebSocket message is not UTF-8") from exc
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ValueError("Binance WebSocket message is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Binance WebSocket message must be an object")
        return parsed

    def process_message(self, message: str | bytes) -> str:
        observed_at_ns = self.clock_ns()
        parsed = self._decode_message(message)
        data = parsed.get("data") if "stream" in parsed else parsed
        if not isinstance(data, dict):
            raise ValueError("Binance combined stream data must be an object")

        event_type = data.get("e")
        if event_type == "aggTrade":
            event = normalize_agg_trade(parsed, observed_at_ns=observed_at_ns, expected_symbol=self.symbol)
            trade_id = event.payload.get("aggregate_trade_id")
            if isinstance(trade_id, bool) or not isinstance(trade_id, int):
                raise ValueError("normalized aggregate trade ID is invalid")
            if self._last_trade_id is not None:
                if trade_id <= self._last_trade_id:
                    self.stats = self.stats.add(stale=1)
                    return "duplicate_or_stale_trade"
                expected = self._last_trade_id + 1
                if trade_id != expected:
                    raise BinanceDataGapError(
                        f"aggregate trade gap: expected {expected}, received {trade_id}"
                    )
            self.store.append(event)
            self._last_trade_id = trade_id
            self.stats = self.stats.add(trades=1)
            return "agg_trade"

        # bookTicker has no `e` field in Binance's documented payload.
        if {"u", "s", "b", "B", "a", "A"}.issubset(data):
            event = normalize_book_ticker(parsed, observed_at_ns=observed_at_ns, expected_symbol=self.symbol)
            update_id = event.payload.get("update_id")
            if isinstance(update_id, bool) or not isinstance(update_id, int):
                raise ValueError("normalized book update ID is invalid")
            if self._last_book_update_id is not None and update_id <= self._last_book_update_id:
                self.stats = self.stats.add(stale=1)
                return "duplicate_or_stale_book"
            self.store.append(event)
            self._last_book_update_id = update_id
            self.stats = self.stats.add(books=1)
            return "book_ticker"

        # Binance may add control/server-shutdown events. They aren't market
        # observations and must not be coerced into the Event Store schema.
        return "ignored_control"

    def run_connection(
        self,
        *,
        connect_fn: ConnectFn = connect,
        open_timeout: float = 10.0,
    ) -> WebSocketIngestStats:
        with connect_fn(
            self.stream_uri,
            open_timeout=open_timeout,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_queue=1024,
        ) as websocket:
            for message in websocket:
                self.process_message(message)
        return self.stats


def catch_up_aggregate_trades(
    ingestor: BinanceMarketWebSocketIngestor,
    rest: BinancePublicRestClient,
    *,
    limit: int = 1000,
    max_batches: int = 100,
) -> int:
    """Repair a WebSocket aggTrade gap from public REST.

    Batches are appended only when they begin exactly at the expected ID. The
    function stops when Binance returns fewer than `limit` rows. A hard batch
    ceiling prevents an accidental unbounded catch-up loop.
    """

    if not 1 <= limit <= 1000:
        raise ValueError("limit must be in [1, 1000]")
    if max_batches <= 0:
        raise ValueError("max_batches must be positive")

    appended = 0
    for _ in range(max_batches):
        from_id = None if ingestor.last_trade_id is None else ingestor.last_trade_id + 1
        rows = rest.collect_aggregate_trades(ingestor.symbol, from_id=from_id, limit=limit)
        if not rows:
            return appended
        for event in rows:
            trade_id = event.payload.get("aggregate_trade_id")
            if isinstance(trade_id, bool) or not isinstance(trade_id, int):
                raise ValueError("REST aggregate trade ID is invalid")
            if ingestor.last_trade_id is not None:
                expected = ingestor.last_trade_id + 1
                if trade_id != expected:
                    raise BinanceDataGapError(
                        f"REST catch-up gap: expected {expected}, received {trade_id}"
                    )
            ingestor.store.append(event)
            ingestor._last_trade_id = trade_id
            ingestor.stats = ingestor.stats.add(trades=1)
            appended += 1
        if len(rows) < limit:
            return appended
    raise BinanceDataGapError("REST catch-up exceeded max_batches")


def run_reconnecting_market_stream(
    ingestor: BinanceMarketWebSocketIngestor,
    *,
    rest: BinancePublicRestClient | None = None,
    connect_fn: ConnectFn = connect,
    sleep: Sleep = time.sleep,
    reconnect_delay_seconds: float = 1.0,
    max_reconnects: int | None = None,
) -> WebSocketIngestStats:
    """Run the market-only stream with bounded-delay reconnects.

    Network/normal closure reconnects. An aggregate-trade continuity failure is
    fatal unless a public REST client is supplied; with REST, the gap is repaired
    before reconnecting. `max_reconnects` exists for controlled service/tests;
    `None` means continuous operation.
    """

    if reconnect_delay_seconds < 0:
        raise ValueError("reconnect_delay_seconds must be non-negative")
    if max_reconnects is not None and max_reconnects < 0:
        raise ValueError("max_reconnects must be non-negative")

    reconnects = 0
    while True:
        try:
            ingestor.run_connection(connect_fn=connect_fn)
        except BinanceDataGapError:
            if rest is None:
                raise
            catch_up_aggregate_trades(ingestor, rest)
        except (ConnectionClosed, OSError, TimeoutError):
            pass

        if max_reconnects is not None and reconnects >= max_reconnects:
            return ingestor.stats
        reconnects += 1
        if reconnect_delay_seconds:
            sleep(reconnect_delay_seconds)
