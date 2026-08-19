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


def normalize_agg_trade(
    message: Mapping[str, Any],
    *,
    observed_at_ns: int,
    expected_symbol: str | None = None,
) -> EventRecord:
    """Normalize a Binance Spot `<symbol>@aggTrade` payload.

    Binance supplies both event time (`E`) and trade time (`T`) in milliseconds.
    The canonical event timestamp uses trade time, while `observed_at_ns` is the
    caller's local arrival timestamp and is never derived from Binance fields.
    """

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
            "exchange_event_time_ms": event_time_ms,
            "trade_time_ms": trade_time_ms,
            "buyer_is_maker": buyer_is_maker,
        },
    )


def normalize_book_ticker(
    message: Mapping[str, Any],
    *,
    observed_at_ns: int,
    expected_symbol: str | None = None,
) -> EventRecord:
    """Normalize a Binance Spot `<symbol>@bookTicker` payload.

    The documented bookTicker payload has no exchange timestamp. The canonical
    event time therefore equals the local observation timestamp instead of
    inventing or borrowing a timestamp from another stream.
    """

    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")
    data = _unwrap(message)
    symbol = _symbol(data.get("s"))
    _check_expected_symbol(symbol, expected_symbol)
    update_id = _non_negative_int(data.get("u"), "book update id")
    bid_price = _positive_decimal(data.get("b"), "bid price")
    bid_quantity = _positive_decimal(data.get("B"), "bid quantity")
    ask_price = _positive_decimal(data.get("a"), "ask price")
    ask_quantity = _positive_decimal(data.get("A"), "ask quantity")
    if bid_price > ask_price:
        raise ValueError("best bid cannot exceed best ask")

    return EventRecord(
        event_id=f"binance:spot:book_ticker:{symbol}:{update_id}",
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
        },
    )
