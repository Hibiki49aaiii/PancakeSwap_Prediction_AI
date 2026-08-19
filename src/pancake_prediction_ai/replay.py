from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .event_store import StoredEvent


@dataclass(frozen=True, slots=True)
class ReplaySnapshot:
    cutoff_ns: int
    events: tuple[StoredEvent, ...]

    def assert_leakage_safe(self) -> None:
        leaked = [
            stored.event.event_id
            for stored in self.events
            if stored.event.observed_at_ns > self.cutoff_ns
        ]
        if leaked:
            raise ValueError(f"future-observed events in snapshot: {leaked}")

    def by_topic(self, topic: str) -> tuple[StoredEvent, ...]:
        return tuple(item for item in self.events if item.event.topic == topic)

    def by_source_topic(self, source: str, topic: str) -> tuple[StoredEvent, ...]:
        return tuple(
            item
            for item in self.events
            if item.event.source == source and item.event.topic == topic
        )


def build_snapshot(events: Iterable[StoredEvent], *, cutoff_ns: int) -> ReplaySnapshot:
    if cutoff_ns < 0:
        raise ValueError("cutoff_ns must be non-negative")
    eligible = tuple(
        sorted(
            (item for item in events if item.event.observed_at_ns <= cutoff_ns),
            key=lambda item: (item.event.observed_at_ns, item.ingest_seq),
        )
    )
    snapshot = ReplaySnapshot(cutoff_ns=cutoff_ns, events=eligible)
    snapshot.assert_leakage_safe()
    return snapshot


def latest_payload(
    snapshot: ReplaySnapshot,
    *,
    source: str,
    topic: str,
) -> Mapping[str, Any] | None:
    snapshot.assert_leakage_safe()
    candidates = snapshot.by_source_topic(source, topic)
    if not candidates:
        return None
    return candidates[-1].event.payload


def latest_numeric(
    snapshot: ReplaySnapshot,
    *,
    source: str,
    topic: str,
    field: str,
) -> float | None:
    payload = latest_payload(snapshot, source=source, topic=topic)
    if payload is None or field not in payload:
        return None
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{source}/{topic}.{field} is not numeric")
    return float(value)


def freshness_ns(
    snapshot: ReplaySnapshot,
    *,
    source: str,
    topic: str,
) -> int | None:
    snapshot.assert_leakage_safe()
    candidates = snapshot.by_source_topic(source, topic)
    if not candidates:
        return None
    observed = candidates[-1].event.observed_at_ns
    return snapshot.cutoff_ns - observed


def fold(
    snapshot: ReplaySnapshot,
    initial: Any,
    reducer: Callable[[Any, StoredEvent], Any],
) -> Any:
    snapshot.assert_leakage_safe()
    state = initial
    for item in snapshot.events:
        state = reducer(state, item)
    return state
