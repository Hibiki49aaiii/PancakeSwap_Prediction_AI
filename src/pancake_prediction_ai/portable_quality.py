from __future__ import annotations

from dataclasses import dataclass

from .portable_features import PortableFeatures
from .replay import ReplaySnapshot


@dataclass(frozen=True, slots=True)
class PortableQualityPolicy:
    max_oracle_age_seconds: float = 30.0
    max_last_trade_age_seconds: float = 5.0
    max_round_observation_age_ns: int = 10_000_000_000
    min_trade_count_long: int = 5
    min_trade_count_short: int = 1
    min_time_to_lock_seconds: float = 5.0
    max_time_to_lock_seconds: float = 60.0
    require_protocol_block_anchor: bool = True
    reject_protocol_anomaly_since_round: bool = True

    def validate(self) -> None:
        if self.max_oracle_age_seconds < 0 or self.max_last_trade_age_seconds < 0:
            raise ValueError("source age thresholds must be non-negative")
        if self.max_round_observation_age_ns < 0:
            raise ValueError("round observation age must be non-negative")
        if self.min_trade_count_long < 0 or self.min_trade_count_short < 0:
            raise ValueError("minimum trade counts must be non-negative")
        if self.min_trade_count_short > self.min_trade_count_long:
            raise ValueError("short-window minimum cannot exceed long-window minimum")
        if self.min_time_to_lock_seconds < 0:
            raise ValueError("min_time_to_lock_seconds must be non-negative")
        if self.max_time_to_lock_seconds < self.min_time_to_lock_seconds:
            raise ValueError("max_time_to_lock_seconds must be >= minimum")


@dataclass(frozen=True, slots=True)
class PortableQualityReport:
    ok: bool
    blockers: tuple[str, ...]
    round_observation_age_ns: int
    latest_protocol_block_number: int | None
    latest_round_block_number: int


def _latest(snapshot: ReplaySnapshot, source: str, topic: str):
    items = snapshot.by_source_topic(source, topic)
    if not items:
        raise ValueError(f"missing required source event: {source}/{topic}")
    return items[-1]


def _int_payload(payload, field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"payload field must be integer: {field}")
    return value


def assess_portable_quality(
    snapshot: ReplaySnapshot,
    features: PortableFeatures,
    *,
    policy: PortableQualityPolicy = PortableQualityPolicy(),
) -> PortableQualityReport:
    snapshot.assert_leakage_safe()
    policy.validate()
    round_snapshot = _latest(
        snapshot,
        "pancake_prediction",
        "prediction.round_snapshot",
    )
    round_age_ns = snapshot.cutoff_ns - round_snapshot.event.observed_at_ns
    if round_age_ns < 0:
        raise ValueError("round snapshot was observed after cutoff")
    round_block = _int_payload(round_snapshot.event.payload, "block_number")

    blockers: list[str] = []
    if features.oracle_age_seconds > policy.max_oracle_age_seconds:
        blockers.append("oracle_stale")
    if features.last_trade_age_seconds > policy.max_last_trade_age_seconds:
        blockers.append("last_trade_stale")
    if round_age_ns > policy.max_round_observation_age_ns:
        blockers.append("round_snapshot_stale")
    if features.trade_count_long < policy.min_trade_count_long:
        blockers.append("insufficient_long_window_trades")
    if features.trade_count_short < policy.min_trade_count_short:
        blockers.append("insufficient_short_window_trades")
    if features.time_to_lock_seconds < policy.min_time_to_lock_seconds:
        blockers.append("decision_too_close_to_lock")
    if features.time_to_lock_seconds > policy.max_time_to_lock_seconds:
        blockers.append("decision_too_early")

    anchors = snapshot.by_source_topic("collector", "collector.protocol_block_anchor")
    latest_anchor_block: int | None = None
    if anchors:
        latest_anchor_block = _int_payload(anchors[-1].event.payload, "block_number")
    if policy.require_protocol_block_anchor:
        if latest_anchor_block is None:
            blockers.append("protocol_block_anchor_missing")
        elif latest_anchor_block != round_block:
            blockers.append("protocol_anchor_round_block_mismatch")

    if policy.reject_protocol_anomaly_since_round:
        anomalies = snapshot.by_source_topic("collector", "collector.protocol_anomaly")
        if any(
            item.event.observed_at_ns >= round_snapshot.event.observed_at_ns
            for item in anomalies
        ):
            blockers.append("protocol_anomaly_since_round_snapshot")

    trades = snapshot.by_source_topic("binance_spot", "market.agg_trade")
    previous_id: int | None = None
    for item in trades:
        trade_id = item.event.payload.get("aggregate_trade_id")
        if isinstance(trade_id, bool) or not isinstance(trade_id, int):
            raise ValueError("aggregate_trade_id must be integer")
        if previous_id is not None and trade_id <= previous_id:
            blockers.append("aggregate_trade_sequence_non_monotonic")
            break
        previous_id = trade_id

    return PortableQualityReport(
        ok=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        round_observation_age_ns=round_age_ns,
        latest_protocol_block_number=latest_anchor_block,
        latest_round_block_number=round_block,
    )
