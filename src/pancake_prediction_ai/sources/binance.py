from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from ..event_store import EventRecord


def _unwrap(message: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept both Binance raw-stream and combined-stream envelopes."""

    if "stream" in message and "data" in message:
        data = message["data"]
        if not isinstance(data, Mapping):
            raise ValueError("combined Binance stream data must be an object")
        return data
    return message


def _symbol(value: object) -> str:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError("Binance symbol must be a non-empty ASCII string")
    return value.upper()


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def _positive_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be positive and finite")
    return parsed


def _check_expected_symbol(symbol: str, expected_symbol: str | None) -> None:
    if expected_symbol is not None and symbol != expected_symbol.upper():
        raise ValueError(f"unexpected Binance symbol: {symbol}")


def _agg_trade_record(
    *,
    symbol: str,
    aggregate_trade_id: int,
    price: Decimal,
    quantity: Decimal,
    first_trade_id: int,
    last_trade_id: int,
    trade_time_ms: int,
    buyer_is_maker: bool,
    observed_at_ns: int,
    exchange_event_time_ms: int | None,
    capture_transport: str,
) -> EventRecord:
    return EventRecord(
        event_id=f"binance:spot:agg_trade:{symbol}:{aggregate_trade_id}",
        source="binance_spot",
        topic="market.agg_trade",
        event_time_ns=trade_time_ms * 1_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "symbol": symbol,
            "aggregate_trade_id": aggregate_trade_id,
            "price": float(price),
            "price_text": format(price, "f"),
            "quantity": float(quantity),
            "quantity_text": format(quantity, "f"),
            "first_trade_id": first_trade_id,
            "last_trade_id": last_trade_id,
            "exchange_event_time_ms": exchange_event_time_ms,
            "trade_time_ms": trade_time_ms,
            "buyer_is_maker": buyer_is_maker,
            "capture_transport": capture_transport,
        },
    )


def normalize_agg_trade(
    message: Mapping[str, Any],
    *,
    observed_at_ns: int,
    expected_symbol: str | None = None,
) -> EventRecord:
    """Normalize a Binance Spot `<symbol>@aggTrade` WebSocket payload."""

    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")
    data = _unwrap(message)
    if data.get("e") != "aggTrade":
        raise ValueError("expected Binance aggTrade event")

    symbol = _symbol(data.get("s"))
    _check_expected_symbol(symbol, expected_symbol)
    aggregate_trade_id = _non_negative_int(data.get("a"), "aggregate trade id")
    event_time_ms = _non_negative_int(data.get("E"), "event time")
    trade_time_ms = _non_negative_int(data.get("T"), "trade time")
    first_trade_id = _non_negative_int(data.get("f"), "first trade id")
    last_trade_id = _non_negative_int(data.get("l"), "last trade id")
    if last_trade_id < first_trade_id:
        raise ValueError("last trade id must be >= first trade id")
    price = _positive_decimal(data.get("p"), "price")
    quantity = _positive_decimal(data.get("q"), "quantity")
    buyer_is_maker = data.get("m")
    if not isinstance(buyer_is_maker, bool):
        raise ValueError("buyer-maker flag must be boolean")

    return _agg_trade_record(
        symbol=symbol,
        aggregate_trade_id=aggregate_trade_id,
        price=price,
        quantity=quantity,
        first_trade_id=first_trade_id,
        last_trade_id=last_trade_id,
        trade_time_ms=trade_time_ms,
        buyer_is_maker=buyer_is_maker,
        observed_at_ns=observed_at_ns,
        exchange_event_time_ms=event_time_ms,
        capture_transport="websocket",
    )


def normalize_rest_agg_trade(
    message: Mapping[str, Any],
    *,
    symbol: str,
    observed_at_ns: int,
) -> EventRecord:
    """Normalize one `GET /api/v3/aggTrades` item.

    `observed_at_ns` must be the local response-arrival time. In particular, a
    historical REST backfill must not copy the old trade timestamp into local
    observation time, because that would falsely claim the data was available
    to this system in the past.
    """

    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")
    normalized_symbol = _symbol(symbol)
    aggregate_trade_id = _non_negative_int(message.get("a"), "aggregate trade id")
    trade_time_ms = _non_negative_int(message.get("T"), "trade time")
    first_trade_id = _non_negative_int(message.get("f"), "first trade id")
    last_trade_id = _non_negative_int(message.get("l"), "last trade id")
    if last_trade_id < first_trade_id:
        raise ValueError("last trade id must be >= first trade id")
    price = _positive_decimal(message.get("p"), "price")
    quantity = _positive_decimal(message.get("q"), "quantity")
    buyer_is_maker = message.get("m")
    if not isinstance(buyer_is_maker, bool):
        raise ValueError("buyer-maker flag must be boolean")
    return _agg_trade_record(
        symbol=normalized_symbol,
        aggregate_trade_id=aggregate_trade_id,
        price=price,
        quantity=quantity,
        first_trade_id=first_trade_id,
        last_trade_id=last_trade_id,
        trade_time_ms=trade_time_ms,
        buyer_is_maker=buyer_is_maker,
        observed_at_ns=observed_at_ns,
        exchange_event_time_ms=None,
        capture_transport="rest",
    )


def _book_record(
    *,
    symbol: str,
    bid_price: Decimal,
    bid_quantity: Decimal,
    ask_price: Decimal,
    ask_quantity: Decimal,
    observed_at_ns: int,
    update_id: int | None,
    capture_transport: str,
) -> EventRecord:
    if bid_price > ask_price:
        raise ValueError("best bid cannot exceed best ask")
    unique = str(update_id) if update_id is not None else f"rest:{observed_at_ns}"
    return EventRecord(
        event_id=f"binance:spot:book_ticker:{symbol}:{unique}",
        source="binance_spot",
        topic="market.book_ticker",
        event_time_ns=observed_at_ns,
        observed_at_ns=observed_at_ns,
        payload={
            "symbol": symbol,
            "update_id": update_id,
            "bid_price": float(bid_price),
            "bid_price_text": format(bid_price, "f"),
            "bid_quantity": float(bid_quantity),
            "bid_quantity_text": format(bid_quantity, "f"),
            "ask_price": float(ask_price),
            "ask_price_text": format(ask_price, "f"),
            "ask_quantity": float(ask_quantity),
            "ask_quantity_text": format(ask_quantity, "f"),
            "mid_price": float((bid_price + ask_price) / Decimal(2)),
            "spread": float(ask_price - bid_price),
            "source_timestamp_available": False,
            "sequence_id_available": update_id is not None,
            "capture_transport": capture_transport,
        },
    )


def normalize_book_ticker(
    message: Mapping[str, Any],
    *,
    observed_at_ns: int,
    expected_symbol: str | None = None,
) -> EventRecord:
    """Normalize a Binance Spot `<symbol>@bookTicker` WebSocket payload.

    The documented WebSocket bookTicker payload has no exchange timestamp. The
    canonical event time therefore equals local observation time.
    """

    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")
    data = _unwrap(message)
    symbol = _symbol(data.get("s"))
    _check_expected_symbol(symbol, expected_symbol)
    update_id = _non_negative_int(data.get("u"), "book update id")
    return _book_record(
        symbol=symbol,
        bid_price=_positive_decimal(data.get("b"), "bid price"),
        bid_quantity=_positive_decimal(data.get("B"), "bid quantity"),
        ask_price=_positive_decimal(data.get("a"), "ask price"),
        ask_quantity=_positive_decimal(data.get("A"), "ask quantity"),
        observed_at_ns=observed_at_ns,
        update_id=update_id,
        capture_transport="websocket",
    )


def normalize_rest_book_ticker(
    message: Mapping[str, Any],
    *,
    observed_at_ns: int,
    expected_symbol: str | None = None,
) -> EventRecord:
    """Normalize `GET /api/v3/ticker/bookTicker` for one symbol.

    REST bookTicker exposes neither a source timestamp nor an update ID. Those
    fields stay explicitly unavailable; no synthetic sequence number is
    invented.
    """

    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")
    symbol = _symbol(message.get("symbol"))
    _check_expected_symbol(symbol, expected_symbol)
    return _book_record(
        symbol=symbol,
        bid_price=_positive_decimal(message.get("bidPrice"), "bid price"),
        bid_quantity=_positive_decimal(message.get("bidQty"), "bid quantity"),
        ask_price=_positive_decimal(message.get("askPrice"), "ask price"),
        ask_quantity=_positive_decimal(message.get("askQty"), "ask quantity"),
        observed_at_ns=observed_at_ns,
        update_id=None,
        capture_transport="rest",
    )
