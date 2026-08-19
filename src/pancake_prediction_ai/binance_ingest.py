from __future__ import annotations

from dataclasses import dataclass

from .binance_public_rest import BinancePublicRestClient
from .event_store import EventRecord, EventStore


class BinanceDataGapError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BinancePollResult:
    trades_appended: int
    last_aggregate_trade_id: int | None
    book_event_id: str


def latest_aggregate_trade_id(store: EventStore, *, symbol: str) -> int | None:
    target = symbol.upper()
    for stored in reversed(store.read_all_ingest_order()):
        event = stored.event
        if event.source != "binance_spot" or event.topic != "market.agg_trade":
            continue
        if event.payload.get("symbol") != target:
            continue
        value = event.payload.get("aggregate_trade_id")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("stored Binance aggregate_trade_id is invalid")
        return value
    return None


def _validate_batch_sequence(events: tuple[EventRecord, ...], *, previous_id: int | None) -> int | None:
    expected = None if previous_id is None else previous_id + 1
    last = previous_id
    for event in events:
        value = event.payload.get("aggregate_trade_id")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("normalized aggregate_trade_id is invalid")
        if expected is not None and value != expected:
            raise BinanceDataGapError(f"aggregate trade gap: expected {expected}, received {value}")
        if last is not None and value <= last:
            raise BinanceDataGapError(f"aggregate trade sequence did not advance: {last} -> {value}")
        last = value
        expected = value + 1
    return last


def poll_binance_public_once(
    client: BinancePublicRestClient,
    store: EventStore,
    *,
    symbol: str = "BNBUSDT",
    trade_limit: int = 1000,
) -> BinancePollResult:
    """Fetch one incremental public-market batch and append it durably.

    On restart, the latest aggregate trade ID is recovered from EventStore and
    the next REST request begins at `last_id + 1`. A sequence gap aborts before
    any event from the suspect trade batch is appended.
    """

    previous_id = latest_aggregate_trade_id(store, symbol=symbol)
    trades = client.collect_aggregate_trades(
        symbol,
        from_id=None if previous_id is None else previous_id + 1,
        limit=trade_limit,
    )
    last_id = _validate_batch_sequence(trades, previous_id=previous_id)

    for event in trades:
        store.append(event)

    book = client.collect_book_ticker(symbol)
    store.append(book)
    return BinancePollResult(
        trades_appended=len(trades),
        last_aggregate_trade_id=last_id,
        book_event_id=book.event_id,
    )
