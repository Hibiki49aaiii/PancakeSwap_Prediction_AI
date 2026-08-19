from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .economics import Outcome
from .event_store import EventRecord, EventStore
from .pancake_contract import BNB_PREDICTION_CONTRACT
from .pancake_events import LifecycleKind, collect_round_lifecycle_logs
from .provenance import ReconstructionPolicy, reconstruct_event
from .read_only_rpc import ReadOnlyJsonRpcClient


@dataclass(frozen=True, slots=True)
class LifecycleBackfillResult:
    dataset_id: str
    from_block: int
    to_block: int
    chunks_completed: int
    events_appended: int


@dataclass(frozen=True, slots=True)
class RoundTimeline:
    epoch: int
    start_event: EventRecord
    lock_event: EventRecord
    end_event: EventRecord
    lock_price: int
    close_price: int
    outcome: Outcome
    lock_available_at_ns: int
    label_available_at_ns: int

    @property
    def lock_timestamp_ns(self) -> int:
        return self.lock_event.event_time_ns

    @property
    def end_timestamp_ns(self) -> int:
        return self.end_event.event_time_ns


@dataclass(frozen=True, slots=True)
class RoundTimelineBuildResult:
    completed: tuple[RoundTimeline, ...]
    incomplete_epochs: tuple[int, ...]


def backfill_round_lifecycle_logs(
    client: ReadOnlyJsonRpcClient,
    store: EventStore,
    *,
    dataset_id: str,
    from_block: int,
    to_block: int,
    assumed_latency_ns: int,
    chunk_size: int = 5_000,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> LifecycleBackfillResult:
    if store.mode != "reconstructed":
        raise ValueError("lifecycle backfill requires reconstructed Event Store")
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if from_block < 0 or to_block < from_block:
        raise ValueError("invalid lifecycle block range")
    if assumed_latency_ns < 0:
        raise ValueError("assumed_latency_ns must be non-negative")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    chunks_completed = 0
    events_appended = 0
    chunk_start = from_block
    while chunk_start <= to_block:
        chunk_end = min(to_block, chunk_start + chunk_size - 1)
        observations = collect_round_lifecycle_logs(
            client,
            from_block=chunk_start,
            to_block=chunk_end,
            prediction_contract=prediction_contract,
        )
        reconstructed: list[EventRecord] = []
        for observation in observations:
            policy = ReconstructionPolicy(
                dataset_id=dataset_id,
                assumed_latency_ns=assumed_latency_ns,
                captured_at_ns=observation.event.observed_at_ns,
            )
            reconstructed.append(
                reconstruct_event(
                    observation.event,
                    policy=policy,
                    availability_base_ns=observation.event.event_time_ns,
                    availability_basis="block_timestamp",
                )
            )
        if reconstructed:
            store.append_many(reconstructed)
            events_appended += len(reconstructed)
        chunks_completed += 1
        chunk_start = chunk_end + 1

    return LifecycleBackfillResult(
        dataset_id=dataset_id,
        from_block=from_block,
        to_block=to_block,
        chunks_completed=chunks_completed,
        events_appended=events_appended,
    )


def _lifecycle_kind(event: EventRecord) -> LifecycleKind:
    if event.source != "pancake_prediction" or event.topic != "prediction.round_lifecycle":
        raise ValueError("event is not a Pancake round lifecycle record")
    value = event.payload.get("kind")
    try:
        return LifecycleKind(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown lifecycle kind: {value}") from exc


def _epoch(event: EventRecord) -> int:
    value = event.payload.get("epoch")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("lifecycle epoch must be non-negative integer")
    return value


def _price(event: EventRecord, field: str) -> int:
    value = event.payload.get("price")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} lifecycle price must be integer")
    return value


def build_round_timelines(events: Iterable[EventRecord]) -> RoundTimelineBuildResult:
    grouped: dict[int, dict[LifecycleKind, EventRecord]] = {}
    for event in events:
        if event.source != "pancake_prediction" or event.topic != "prediction.round_lifecycle":
            continue
        kind = _lifecycle_kind(event)
        epoch = _epoch(event)
        per_epoch = grouped.setdefault(epoch, {})
        if kind in per_epoch:
            raise ValueError(f"duplicate {kind.value} lifecycle event for epoch {epoch}")
        per_epoch[kind] = event

    completed: list[RoundTimeline] = []
    incomplete: list[int] = []
    for epoch in sorted(grouped):
        per_epoch = grouped[epoch]
        if set(per_epoch) != {LifecycleKind.START, LifecycleKind.LOCK, LifecycleKind.END}:
            incomplete.append(epoch)
            continue
        start = per_epoch[LifecycleKind.START]
        lock = per_epoch[LifecycleKind.LOCK]
        end = per_epoch[LifecycleKind.END]
        if not (start.event_time_ns <= lock.event_time_ns <= end.event_time_ns):
            raise ValueError(f"non-chronological lifecycle events for epoch {epoch}")
        if not (start.observed_at_ns <= lock.observed_at_ns <= end.observed_at_ns):
            raise ValueError(f"non-chronological lifecycle availability for epoch {epoch}")

        lock_price = _price(lock, "LOCK")
        close_price = _price(end, "END")
        if close_price > lock_price:
            outcome = Outcome.BULL
        elif close_price < lock_price:
            outcome = Outcome.BEAR
        else:
            outcome = Outcome.TIE

        completed.append(
            RoundTimeline(
                epoch=epoch,
                start_event=start,
                lock_event=lock,
                end_event=end,
                lock_price=lock_price,
                close_price=close_price,
                outcome=outcome,
                lock_available_at_ns=lock.observed_at_ns,
                label_available_at_ns=end.observed_at_ns,
            )
        )

    return RoundTimelineBuildResult(
        completed=tuple(completed),
        incomplete_epochs=tuple(incomplete),
    )
