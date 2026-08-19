from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .event_store import EventStore
from .onchain_collector import (
    read_prediction_buffer_seconds_at_anchor,
    read_prediction_round_at_anchor,
)
from .pancake_contract import BNB_PREDICTION_CONTRACT, PredictionRoundState
from .read_only_rpc import ReadOnlyJsonRpcClient
from .rpc_snapshot import BlockAnchor, fetch_block_anchor
from .shadow_settlement import (
    ShadowEconomicSettlementResult,
    reconcile_shadow_economic_round,
)


ClockNs = Callable[[], int]
RoundReader = Callable[..., PredictionRoundState]
BufferReader = Callable[..., int]


@dataclass(frozen=True, slots=True)
class ShadowSettlementBatchResult:
    anchor: BlockAnchor | None
    attempted_round_ids: tuple[int, ...]
    results: tuple[ShadowEconomicSettlementResult, ...]


def unresolved_shadow_economic_round_ids(store: EventStore) -> tuple[int, ...]:
    if store.mode != "observed":
        raise ValueError("shadow settlement scan requires observed Event Store")
    decisions: set[int] = set()
    settlements: set[int] = set()
    for stored in store.read_all_ingest_order():
        event = stored.event
        if event.source != "shadow":
            continue
        if event.topic not in {"shadow.economic_decision", "shadow.economic_settlement"}:
            continue
        value = event.payload.get("round_id")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("shadow economic round_id is invalid")
        if event.topic == "shadow.economic_decision":
            if value in decisions:
                raise ValueError(f"duplicate shadow economic decision for round {value}")
            decisions.add(value)
        else:
            if value in settlements:
                raise ValueError(f"duplicate shadow economic settlement for round {value}")
            settlements.add(value)
    orphaned = settlements - decisions
    if orphaned:
        raise ValueError(f"shadow settlements without decisions: {sorted(orphaned)}")
    return tuple(sorted(decisions - settlements))


def reconcile_pending_shadow_economic_rounds(
    store: EventStore,
    client: ReadOnlyJsonRpcClient,
    *,
    max_rounds: int | None = None,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
    anchor_fetcher: Callable[[ReadOnlyJsonRpcClient], BlockAnchor] = fetch_block_anchor,
    round_reader: RoundReader = read_prediction_round_at_anchor,
    buffer_reader: BufferReader = read_prediction_buffer_seconds_at_anchor,
) -> ShadowSettlementBatchResult:
    """Reconcile unresolved paper rounds against one shared current BSC anchor."""

    if max_rounds is not None and max_rounds <= 0:
        raise ValueError("max_rounds must be positive")
    pending = unresolved_shadow_economic_round_ids(store)
    if max_rounds is not None:
        pending = pending[:max_rounds]
    if not pending:
        return ShadowSettlementBatchResult(None, (), ())

    anchor = anchor_fetcher(client)
    results: list[ShadowEconomicSettlementResult] = []
    for round_id in pending:
        results.append(
            reconcile_shadow_economic_round(
                store,
                client,
                round_id=round_id,
                prediction_contract=prediction_contract,
                clock_ns=clock_ns,
                anchor_fetcher=lambda _client, pinned=anchor: pinned,
                round_reader=round_reader,
                buffer_reader=buffer_reader,
            )
        )
    return ShadowSettlementBatchResult(
        anchor=anchor,
        attempted_round_ids=pending,
        results=tuple(results),
    )
