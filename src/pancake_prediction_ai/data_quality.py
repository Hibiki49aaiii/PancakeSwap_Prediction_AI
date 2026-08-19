from __future__ import annotations

from dataclasses import dataclass

from .features import CoreFeatures
from .replay import ReplaySnapshot


@dataclass(frozen=True, slots=True)
class SourceQualityPolicy:
    max_book_observation_age_ns: int
    max_oracle_source_age_ns: int
    max_round_observation_age_ns: int
    max_spread_bps: float
    min_recent_trade_count: int
    min_time_to_lock_seconds: float
    require_book_sequence_id: bool = True

    def validate(self) -> None:
        if min(
            self.max_book_observation_age_ns,
            self.max_oracle_source_age_ns,
            self.max_round_observation_age_ns,
            self.min_recent_trade_count,
        ) < 0:
            raise ValueError("quality age/count thresholds must be non-negative")
        if self.max_spread_bps < 0:
            raise ValueError("max_spread_bps must be non-negative")
        if self.min_time_to_lock_seconds < 0:
            raise ValueError("min_time_to_lock_seconds must be non-negative")


@dataclass(frozen=True, slots=True)
class SourceQualityReport:
    ok: bool
    blockers: tuple[str, ...]
    book_observation_age_ns: int
    oracle_source_age_ns: int
    round_observation_age_ns: int


def _latest(snapshot: ReplaySnapshot, source: str, topic: str):
    items = snapshot.by_source_topic(source, topic)
    if not items:
        raise ValueError(f"missing required source event: {source}/{topic}")
    return items[-1]


def _sequence_status(snapshot: ReplaySnapshot, source: str, topic: str, field: str) -> str:
    """Return `ok`, `unavailable`, or `non_monotonic` for one source sequence."""

    previous: int | None = None
    for item in snapshot.by_source_topic(source, topic):
        value = item.event.payload.get(field)
        if value is None:
            return "unavailable"
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"sequence field must be integer or null: {source}/{topic}.{field}")
        if previous is not None and value <= previous:
            return "non_monotonic"
        previous = value
    return "ok"


def assess_source_quality(
    snapshot: ReplaySnapshot,
    features: CoreFeatures,
    *,
    policy: SourceQualityPolicy,
) -> SourceQualityReport:
    snapshot.assert_leakage_safe()
    policy.validate()

    book = _latest(snapshot, "binance_spot", "market.book_ticker")
    oracle = _latest(snapshot, "chainlink", "oracle.latest_round")
    round_snapshot = _latest(snapshot, "pancake_prediction", "prediction.round_snapshot")

    book_age = snapshot.cutoff_ns - book.event.observed_at_ns
    oracle_age = snapshot.cutoff_ns - oracle.event.event_time_ns
    round_age = snapshot.cutoff_ns - round_snapshot.event.observed_at_ns
    if min(book_age, round_age) < 0:
        raise ValueError("snapshot contains future-observed source event")

    blockers: list[str] = []
    if book_age > policy.max_book_observation_age_ns:
        blockers.append("binance_book_stale")
    if oracle_age < 0 or oracle_age > policy.max_oracle_source_age_ns:
        blockers.append("chainlink_oracle_stale")
    if round_age > policy.max_round_observation_age_ns:
        blockers.append("pancake_round_snapshot_stale")
    if features.binance_spread_bps > policy.max_spread_bps:
        blockers.append("binance_spread_too_wide")
    if features.trade_count < policy.min_recent_trade_count:
        blockers.append("insufficient_recent_trades")
    if features.time_to_lock_seconds < policy.min_time_to_lock_seconds:
        blockers.append("decision_too_close_to_lock")

    book_sequence = _sequence_status(snapshot, "binance_spot", "market.book_ticker", "update_id")
    if book_sequence == "non_monotonic":
        blockers.append("binance_book_sequence_non_monotonic")
    elif book_sequence == "unavailable" and policy.require_book_sequence_id:
        blockers.append("binance_book_sequence_unavailable")

    if _sequence_status(snapshot, "binance_spot", "market.agg_trade", "aggregate_trade_id") == "non_monotonic":
        blockers.append("binance_trade_sequence_non_monotonic")

    return SourceQualityReport(
        ok=not blockers,
        blockers=tuple(blockers),
        book_observation_age_ns=book_age,
        oracle_source_age_ns=oracle_age,
        round_observation_age_ns=round_age,
    )
