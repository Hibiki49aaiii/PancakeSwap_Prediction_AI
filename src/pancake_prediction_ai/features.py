from __future__ import annotations

from dataclasses import dataclass

from .event_store import StoredEvent
from .replay import ReplaySnapshot


@dataclass(frozen=True, slots=True)
class CoreFeatures:
    binance_mid_price: float
    binance_spread_bps: float
    chainlink_price: float
    binance_chainlink_divergence_bps: float
    oracle_age_seconds: float
    aggressor_flow_ratio: float
    aggressor_notional: float
    trade_count: int
    pancake_bull_share: float
    pancake_pool_imbalance: float
    pancake_total_amount_wei: int
    time_to_lock_seconds: float

    def as_dict(self) -> dict[str, float]:
        return {
            "binance_mid_price": self.binance_mid_price,
            "binance_spread_bps": self.binance_spread_bps,
            "chainlink_price": self.chainlink_price,
            "binance_chainlink_divergence_bps": self.binance_chainlink_divergence_bps,
            "oracle_age_seconds": self.oracle_age_seconds,
            "aggressor_flow_ratio": self.aggressor_flow_ratio,
            "aggressor_notional": self.aggressor_notional,
            "trade_count": float(self.trade_count),
            "pancake_bull_share": self.pancake_bull_share,
            "pancake_pool_imbalance": self.pancake_pool_imbalance,
            "pancake_total_amount_wei": float(self.pancake_total_amount_wei),
            "time_to_lock_seconds": self.time_to_lock_seconds,
        }


def _latest(snapshot: ReplaySnapshot, *, source: str, topic: str) -> StoredEvent:
    snapshot.assert_leakage_safe()
    items = snapshot.by_source_topic(source, topic)
    if not items:
        raise ValueError(f"missing required source event: {source}/{topic}")
    return items[-1]


def _numeric(payload: object, field: str) -> float:
    if not isinstance(payload, dict) or field not in payload:
        raise ValueError(f"missing numeric payload field: {field}")
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"payload field is not numeric: {field}")
    return float(value)


def build_core_features(
    snapshot: ReplaySnapshot,
    *,
    trade_window_ns: int = 30_000_000_000,
    max_source_clock_skew_ns: int = 2_000_000_000,
) -> CoreFeatures:
    """Build the first canonical feature family from a leakage-safe snapshot.

    Aggressor sign follows Binance's `buyer_is_maker` flag: if the buyer is
    maker, the taker/aggressor sold into the bid (negative); otherwise the
    aggressor bought from the ask (positive).
    """

    snapshot.assert_leakage_safe()
    if trade_window_ns <= 0:
        raise ValueError("trade_window_ns must be positive")
    if max_source_clock_skew_ns < 0:
        raise ValueError("max_source_clock_skew_ns must be non-negative")

    book = _latest(snapshot, source="binance_spot", topic="market.book_ticker")
    oracle = _latest(snapshot, source="chainlink", topic="oracle.latest_round")
    round_snapshot = _latest(snapshot, source="pancake_prediction", topic="prediction.round_snapshot")

    mid = _numeric(book.event.payload, "mid_price")
    spread = _numeric(book.event.payload, "spread")
    chainlink_price = _numeric(oracle.event.payload, "price")
    if mid <= 0 or chainlink_price <= 0:
        raise ValueError("market/oracle prices must be positive")
    spread_bps = spread / mid * 10_000.0
    divergence_bps = (mid - chainlink_price) / chainlink_price * 10_000.0

    oracle_age_ns = snapshot.cutoff_ns - oracle.event.event_time_ns
    if oracle_age_ns < -max_source_clock_skew_ns:
        raise ValueError("Chainlink source timestamp is materially after decision cutoff")
    oracle_age_seconds = max(0, oracle_age_ns) / 1_000_000_000

    window_start = snapshot.cutoff_ns - trade_window_ns
    signed_notional = 0.0
    total_notional = 0.0
    trade_count = 0
    for item in snapshot.by_source_topic("binance_spot", "market.agg_trade"):
        if item.event.event_time_ns < window_start or item.event.event_time_ns > snapshot.cutoff_ns:
            continue
        payload = item.event.payload
        price = _numeric(payload, "price")
        quantity = _numeric(payload, "quantity")
        buyer_is_maker = payload.get("buyer_is_maker") if isinstance(payload, dict) else None
        if not isinstance(buyer_is_maker, bool):
            raise ValueError("agg trade buyer_is_maker must be boolean")
        notional = price * quantity
        direction = -1.0 if buyer_is_maker else 1.0
        signed_notional += direction * notional
        total_notional += notional
        trade_count += 1
    aggressor_flow_ratio = signed_notional / total_notional if total_notional > 0 else 0.0

    bull = int(_numeric(round_snapshot.event.payload, "bull_amount_wei"))
    bear = int(_numeric(round_snapshot.event.payload, "bear_amount_wei"))
    total = int(_numeric(round_snapshot.event.payload, "total_amount_wei"))
    if bull < 0 or bear < 0 or total < 0 or bull + bear != total:
        raise ValueError("Pancake pool snapshot invariant failed")
    if total == 0:
        bull_share = 0.5
        pool_imbalance = 0.0
    else:
        bull_share = bull / total
        pool_imbalance = (bull - bear) / total

    lock_timestamp_s = _numeric(round_snapshot.event.payload, "lock_timestamp")
    cutoff_s = snapshot.cutoff_ns / 1_000_000_000
    time_to_lock = lock_timestamp_s - cutoff_s

    return CoreFeatures(
        binance_mid_price=mid,
        binance_spread_bps=spread_bps,
        chainlink_price=chainlink_price,
        binance_chainlink_divergence_bps=divergence_bps,
        oracle_age_seconds=oracle_age_seconds,
        aggressor_flow_ratio=aggressor_flow_ratio,
        aggressor_notional=signed_notional,
        trade_count=trade_count,
        pancake_bull_share=bull_share,
        pancake_pool_imbalance=pool_imbalance,
        pancake_total_amount_wei=total,
        time_to_lock_seconds=time_to_lock,
    )
