from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .event_store import StoredEvent
from .replay import ReplaySnapshot


@dataclass(frozen=True, slots=True)
class PortableFeatures:
    binance_last_trade_price: float
    chainlink_price: float
    binance_chainlink_divergence_bps: float
    oracle_age_seconds: float
    last_trade_age_seconds: float
    trade_count_long: int
    trade_count_short: int
    aggressor_flow_ratio_long: float
    aggressor_flow_ratio_short: float
    price_change_bps_long: float
    price_change_bps_short: float
    realized_volatility_bps_long: float
    pancake_bull_share: float
    pancake_pool_imbalance: float
    pancake_total_amount_wei: int
    time_to_lock_seconds: float

    def as_dict(self) -> dict[str, float]:
        return {
            "binance_last_trade_price": self.binance_last_trade_price,
            "chainlink_price": self.chainlink_price,
            "binance_chainlink_divergence_bps": self.binance_chainlink_divergence_bps,
            "oracle_age_seconds": self.oracle_age_seconds,
            "last_trade_age_seconds": self.last_trade_age_seconds,
            "trade_count_long": float(self.trade_count_long),
            "trade_count_short": float(self.trade_count_short),
            "aggressor_flow_ratio_long": self.aggressor_flow_ratio_long,
            "aggressor_flow_ratio_short": self.aggressor_flow_ratio_short,
            "price_change_bps_long": self.price_change_bps_long,
            "price_change_bps_short": self.price_change_bps_short,
            "realized_volatility_bps_long": self.realized_volatility_bps_long,
            "pancake_bull_share": self.pancake_bull_share,
            "pancake_pool_imbalance": self.pancake_pool_imbalance,
            "pancake_log_total_amount": math.log1p(self.pancake_total_amount_wei),
            "time_to_lock_seconds": self.time_to_lock_seconds,
        }


@dataclass(frozen=True, slots=True)
class PortableFeaturePolicy:
    long_window_ns: int = 30_000_000_000
    short_window_ns: int = 5_000_000_000
    max_source_clock_skew_ns: int = 2_000_000_000

    def validate(self) -> None:
        if self.long_window_ns <= 0 or self.short_window_ns <= 0:
            raise ValueError("feature windows must be positive")
        if self.short_window_ns > self.long_window_ns:
            raise ValueError("short window cannot exceed long window")
        if self.max_source_clock_skew_ns < 0:
            raise ValueError("max_source_clock_skew_ns must be non-negative")


def _latest(snapshot: ReplaySnapshot, *, source: str, topic: str) -> StoredEvent:
    snapshot.assert_leakage_safe()
    items = snapshot.by_source_topic(source, topic)
    if not items:
        raise ValueError(f"missing required source event: {source}/{topic}")
    return items[-1]


def _numeric(payload: Mapping[str, object], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"payload field is not numeric: {field}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"payload field is not finite: {field}")
    return result


def _integer(payload: Mapping[str, object], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"payload field is not integer: {field}")
    return value


def _window_trades(
    snapshot: ReplaySnapshot,
    *,
    window_ns: int,
) -> tuple[StoredEvent, ...]:
    start = snapshot.cutoff_ns - window_ns
    trades = [
        item
        for item in snapshot.by_source_topic("binance_spot", "market.agg_trade")
        if start <= item.event.event_time_ns <= snapshot.cutoff_ns
    ]
    trades.sort(key=lambda item: (item.event.event_time_ns, item.ingest_seq))
    return tuple(trades)


def _flow_ratio(trades: tuple[StoredEvent, ...]) -> float:
    signed = 0.0
    total = 0.0
    for item in trades:
        price = _numeric(item.event.payload, "price")
        quantity = _numeric(item.event.payload, "quantity")
        buyer_is_maker = item.event.payload.get("buyer_is_maker")
        if not isinstance(buyer_is_maker, bool):
            raise ValueError("agg trade buyer_is_maker must be boolean")
        notional = price * quantity
        signed += (-1.0 if buyer_is_maker else 1.0) * notional
        total += notional
    return signed / total if total > 0 else 0.0


def _price_change_bps(trades: tuple[StoredEvent, ...]) -> float:
    if len(trades) < 2:
        return 0.0
    first = _numeric(trades[0].event.payload, "price")
    last = _numeric(trades[-1].event.payload, "price")
    if first <= 0:
        raise ValueError("first trade price must be positive")
    return (last - first) / first * 10_000.0


def _realized_volatility_bps(trades: tuple[StoredEvent, ...]) -> float:
    if len(trades) < 2:
        return 0.0
    prices = tuple(_numeric(item.event.payload, "price") for item in trades)
    if any(price <= 0 for price in prices):
        raise ValueError("trade prices must be positive")
    log_returns = tuple(
        math.log(current / previous)
        for previous, current in zip(prices, prices[1:])
    )
    return math.sqrt(sum(value * value for value in log_returns)) * 10_000.0


def build_portable_features(
    snapshot: ReplaySnapshot,
    *,
    policy: PortableFeaturePolicy = PortableFeaturePolicy(),
) -> PortableFeatures:
    """Build features available from both public historical and live sources."""

    snapshot.assert_leakage_safe()
    policy.validate()
    oracle = _latest(snapshot, source="chainlink", topic="oracle.latest_round")
    round_snapshot = _latest(
        snapshot,
        source="pancake_prediction",
        topic="prediction.round_snapshot",
    )
    long_trades = _window_trades(snapshot, window_ns=policy.long_window_ns)
    short_trades = _window_trades(snapshot, window_ns=policy.short_window_ns)
    if not long_trades:
        raise ValueError("missing Binance aggregate trades in long feature window")

    last_trade = long_trades[-1]
    last_trade_price = _numeric(last_trade.event.payload, "price")
    chainlink_price = _numeric(oracle.event.payload, "price")
    if last_trade_price <= 0 or chainlink_price <= 0:
        raise ValueError("market/oracle prices must be positive")

    oracle_age_ns = snapshot.cutoff_ns - oracle.event.event_time_ns
    if oracle_age_ns < -policy.max_source_clock_skew_ns:
        raise ValueError("Chainlink source timestamp is materially after cutoff")
    last_trade_age_ns = snapshot.cutoff_ns - last_trade.event.event_time_ns
    if last_trade_age_ns < -policy.max_source_clock_skew_ns:
        raise ValueError("Binance trade timestamp is materially after cutoff")

    bull = _integer(round_snapshot.event.payload, "bull_amount_wei")
    bear = _integer(round_snapshot.event.payload, "bear_amount_wei")
    total = _integer(round_snapshot.event.payload, "total_amount_wei")
    if bull < 0 or bear < 0 or total < 0 or bull + bear != total:
        raise ValueError("Pancake pool snapshot invariant failed")
    if total:
        bull_share = bull / total
        pool_imbalance = (bull - bear) / total
    else:
        bull_share = 0.5
        pool_imbalance = 0.0

    lock_timestamp_s = _numeric(round_snapshot.event.payload, "lock_timestamp")
    time_to_lock = lock_timestamp_s - snapshot.cutoff_ns / 1_000_000_000

    return PortableFeatures(
        binance_last_trade_price=last_trade_price,
        chainlink_price=chainlink_price,
        binance_chainlink_divergence_bps=(
            (last_trade_price - chainlink_price) / chainlink_price * 10_000.0
        ),
        oracle_age_seconds=max(0, oracle_age_ns) / 1_000_000_000,
        last_trade_age_seconds=max(0, last_trade_age_ns) / 1_000_000_000,
        trade_count_long=len(long_trades),
        trade_count_short=len(short_trades),
        aggressor_flow_ratio_long=_flow_ratio(long_trades),
        aggressor_flow_ratio_short=_flow_ratio(short_trades),
        price_change_bps_long=_price_change_bps(long_trades),
        price_change_bps_short=_price_change_bps(short_trades),
        realized_volatility_bps_long=_realized_volatility_bps(long_trades),
        pancake_bull_share=bull_share,
        pancake_pool_imbalance=pool_imbalance,
        pancake_total_amount_wei=total,
        time_to_lock_seconds=time_to_lock,
    )
