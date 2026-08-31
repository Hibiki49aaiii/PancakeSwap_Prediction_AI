from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

PRICE_SCALE = 100_000_000
QTY_SCALE = 100_000_000
PPM = 1_000_000


@dataclass(frozen=True, slots=True)
class AggTrade:
    symbol: str
    event_timestamp_ms: int
    trade_timestamp_ms: int
    price_e8: int
    quantity_e8: int
    aggressive_side: str
    aggregate_trade_id: int

    @property
    def quote_notional_e16(self) -> int:
        return self.price_e8 * self.quantity_e8


@dataclass(frozen=True, slots=True)
class OrderFlowWindow:
    start_timestamp_ms: int
    end_timestamp_ms: int
    buy_notional_e16: int
    sell_notional_e16: int
    trade_count: int
    last_price_e8: int | None

    @property
    def imbalance_ppm(self) -> int | None:
        total = self.buy_notional_e16 + self.sell_notional_e16
        if total <= 0:
            return None
        return (self.buy_notional_e16 - self.sell_notional_e16) * PPM // total


def decimal_to_fixed(value: object, *, scale: int) -> int:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"decimal value must be finite and non-negative: {value!r}")
    return int((number * scale).to_integral_value(rounding=ROUND_DOWN))


def parse_agg_trade(message: dict[str, Any]) -> AggTrade:
    if message.get("e") not in (None, "aggTrade"):
        raise ValueError("message is not a Binance aggTrade")
    required = ("s", "E", "T", "p", "q", "m", "a")
    missing = [key for key in required if key not in message]
    if missing:
        raise ValueError(f"aggTrade missing fields: {','.join(missing)}")
    event_timestamp_ms = int(message["E"])
    trade_timestamp_ms = int(message["T"])
    if event_timestamp_ms < 0 or trade_timestamp_ms < 0:
        raise ValueError("timestamps must be non-negative")
    buyer_is_maker = bool(message["m"])
    return AggTrade(
        symbol=str(message["s"]).upper(),
        event_timestamp_ms=event_timestamp_ms,
        trade_timestamp_ms=trade_timestamp_ms,
        price_e8=decimal_to_fixed(message["p"], scale=PRICE_SCALE),
        quantity_e8=decimal_to_fixed(message["q"], scale=QTY_SCALE),
        aggressive_side="sell" if buyer_is_maker else "buy",
        aggregate_trade_id=int(message["a"]),
    )


def aggregate_order_flow(
    trades: Iterable[AggTrade], *, start_timestamp_ms: int, end_timestamp_ms: int
) -> OrderFlowWindow:
    if start_timestamp_ms < 0 or end_timestamp_ms <= start_timestamp_ms:
        raise ValueError("invalid order-flow window")
    buy = 0
    sell = 0
    count = 0
    last_price: int | None = None
    last_timestamp = -1
    for trade in trades:
        if (
            trade.trade_timestamp_ms < start_timestamp_ms
            or trade.trade_timestamp_ms >= end_timestamp_ms
            or trade.event_timestamp_ms >= end_timestamp_ms
        ):
            continue
        count += 1
        if trade.aggressive_side == "buy":
            buy += trade.quote_notional_e16
        elif trade.aggressive_side == "sell":
            sell += trade.quote_notional_e16
        else:
            raise ValueError(f"unknown aggressive side: {trade.aggressive_side}")
        if trade.trade_timestamp_ms >= last_timestamp:
            last_timestamp = trade.trade_timestamp_ms
            last_price = trade.price_e8
    return OrderFlowWindow(
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=end_timestamp_ms,
        buy_notional_e16=buy,
        sell_notional_e16=sell,
        trade_count=count,
        last_price_e8=last_price,
    )
