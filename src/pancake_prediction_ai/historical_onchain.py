from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .event_store import EventRecord, EventStore, StoredEvent
from .onchain_collector import PinnedProtocolSnapshot, collect_protocol_snapshot_at_anchor
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT
from .provenance import ReconstructionPolicy, reconstruct_event
from .read_only_rpc import ReadOnlyJsonRpcClient
from .rpc_snapshot import BlockAnchor, find_block_at_or_before_timestamp


ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class HistoricalProtocolPoint:
    target_timestamp_s: int
    anchor: BlockAnchor
    epoch: int
    reconstructed_observed_at_ns: int
    stored_events: tuple[StoredEvent, ...]


@dataclass(frozen=True, slots=True)
class HistoricalProtocolBackfillResult:
    dataset_id: str
    points: tuple[HistoricalProtocolPoint, ...]
    duplicate_anchor_targets_skipped: int

    @property
    def events_appended(self) -> int:
        return sum(len(point.stored_events) for point in self.points)


def reconstruct_protocol_snapshot(
    snapshot: PinnedProtocolSnapshot,
    *,
    dataset_id: str,
    assumed_latency_ns: int,
) -> tuple[EventRecord, ...]:
    """Convert one archive-RPC snapshot into explicit reconstructed evidence.

    All events become available from the same containing block timestamp plus
    the configured latency. Chainlink's own `updatedAt` remains event_time for
    freshness features; it is not incorrectly reused as reconstructed system
    availability.
    """

    snapshot.validate()
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if assumed_latency_ns < 0:
        raise ValueError("assumed_latency_ns must be non-negative")
    captured_at_ns = snapshot.events[0].observed_at_ns
    if any(event.observed_at_ns != captured_at_ns for event in snapshot.events):
        raise ValueError("source snapshot has inconsistent capture timestamps")

    policy = ReconstructionPolicy(
        dataset_id=dataset_id,
        assumed_latency_ns=assumed_latency_ns,
        captured_at_ns=captured_at_ns,
    )
    block_base_ns = snapshot.anchor.timestamp_s * 1_000_000_000
    return tuple(
        reconstruct_event(
            event,
            policy=policy,
            availability_base_ns=block_base_ns,
            availability_basis="block_timestamp",
        )
        for event in snapshot.events
    )


def backfill_protocol_at_timestamps(
    client: ReadOnlyJsonRpcClient,
    store: EventStore,
    *,
    dataset_id: str,
    target_timestamps_s: Iterable[int],
    lower_block: int,
    upper_block: int,
    assumed_latency_ns: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> HistoricalProtocolBackfillResult:
    """Reconstruct protocol state at requested wall-clock decision timestamps.

    Each target maps to the latest block whose timestamp is not after the target.
    Repeated targets mapping to the same block are de-duplicated because the
    underlying pinned protocol snapshot would be identical.
    """

    if store.mode != "reconstructed":
        raise ValueError("historical protocol backfill requires reconstructed Event Store")
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if lower_block < 0 or upper_block < lower_block:
        raise ValueError("invalid block bounds")
    if assumed_latency_ns < 0:
        raise ValueError("assumed_latency_ns must be non-negative")

    targets = tuple(int(value) for value in target_timestamps_s)
    if not targets:
        raise ValueError("at least one target timestamp is required")
    if any(value <= 0 for value in targets):
        raise ValueError("target timestamps must be positive")
    if any(current <= previous for previous, current in zip(targets, targets[1:])):
        raise ValueError("target timestamps must be strictly increasing")

    chain_id = client.chain_id()
    if chain_id != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}, got {chain_id}")

    points: list[HistoricalProtocolPoint] = []
    seen_blocks: set[int] = set()
    skipped = 0
    for target_timestamp_s in targets:
        anchor = find_block_at_or_before_timestamp(
            client,
            target_timestamp_s=target_timestamp_s,
            lower_block=lower_block,
            upper_block=upper_block,
        )
        if anchor.number in seen_blocks:
            skipped += 1
            continue
        seen_blocks.add(anchor.number)

        source_snapshot = collect_protocol_snapshot_at_anchor(
            client,
            anchor=anchor,
            prediction_contract=prediction_contract,
            clock_ns=clock_ns,
        )
        reconstructed = reconstruct_protocol_snapshot(
            source_snapshot,
            dataset_id=dataset_id,
            assumed_latency_ns=assumed_latency_ns,
        )
        stored = store.append_many(reconstructed)
        points.append(
            HistoricalProtocolPoint(
                target_timestamp_s=target_timestamp_s,
                anchor=anchor,
                epoch=source_snapshot.current_epoch,
                reconstructed_observed_at_ns=(
                    anchor.timestamp_s * 1_000_000_000 + assumed_latency_ns
                ),
                stored_events=stored,
            )
        )

    return HistoricalProtocolBackfillResult(
        dataset_id=dataset_id,
        points=tuple(points),
        duplicate_anchor_targets_skipped=skipped,
    )
