from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .event_store import EventStore, StoredEvent
from .onchain_collector import PinnedProtocolSnapshot, collect_pinned_protocol_snapshot
from .pancake_contract import BNB_PREDICTION_CONTRACT
from .read_only_rpc import ReadOnlyJsonRpcClient


ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ProtocolIngestResult:
    snapshot: PinnedProtocolSnapshot
    stored_events: tuple[StoredEvent, ...]


def collect_and_persist_protocol_snapshot(
    client: ReadOnlyJsonRpcClient,
    store: EventStore,
    *,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> ProtocolIngestResult:
    snapshot = collect_pinned_protocol_snapshot(
        client,
        prediction_contract=prediction_contract,
        clock_ns=clock_ns,
    )
    stored = store.append_many(snapshot.events)
    if len(stored) != len(snapshot.events):
        raise AssertionError("protocol snapshot persistence count mismatch")
    return ProtocolIngestResult(snapshot=snapshot, stored_events=stored)
