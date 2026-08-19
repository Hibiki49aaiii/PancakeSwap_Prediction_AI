from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .binance_ingest import BinancePollResult, poll_binance_public_once
from .binance_public_rest import BinancePublicRestClient
from .event_store import EventStore
from .onchain_ingest import ProtocolIngestResult, collect_and_persist_protocol_snapshot
from .read_only_rpc import ReadOnlyJsonRpcClient
from .shadow_inference import ShadowInferenceResult, infer_shadow_decision
from .trained_model_artifact import PromotedModelArtifact


ClockNs = Callable[[], int]
BinanceCollector = Callable[..., BinancePollResult]
ProtocolCollector = Callable[..., ProtocolIngestResult]
InferenceRunner = Callable[..., ShadowInferenceResult]


@dataclass(frozen=True, slots=True)
class ObservedShadowCycleResult:
    binance: BinancePollResult
    protocol: ProtocolIngestResult
    inference: ShadowInferenceResult
    store_tip_hash: str
    store_event_count: int


def run_observed_shadow_cycle(
    store: EventStore,
    artifact: PromotedModelArtifact,
    *,
    binance_client: BinancePublicRestClient,
    rpc_client: ReadOnlyJsonRpcClient,
    symbol: str = "BNBUSDT",
    trade_limit: int = 1000,
    clock_ns: ClockNs = time.time_ns,
    binance_collector: BinanceCollector = poll_binance_public_once,
    protocol_collector: ProtocolCollector = collect_and_persist_protocol_snapshot,
    inference_runner: InferenceRunner = infer_shadow_decision,
) -> ObservedShadowCycleResult:
    """Run one signer-free observation -> inference cycle.

    The cycle only consumes public Binance market data and allowlisted read-only
    BSC JSON-RPC calls. Every observation is persisted to the same append-only
    Event Store before inference. The model decision is therefore bound to a
    concrete hash-chain tip rather than to transient in-memory data.
    """

    if store.mode != "observed":
        raise ValueError("observed shadow cycle requires observed Event Store")
    if not symbol.strip():
        raise ValueError("symbol is required")
    if not 1 <= trade_limit <= 1000:
        raise ValueError("trade_limit must be in [1, 1000]")
    artifact.validate()

    binance = binance_collector(
        binance_client,
        store,
        symbol=symbol,
        trade_limit=trade_limit,
    )
    protocol = protocol_collector(
        rpc_client,
        store,
        clock_ns=clock_ns,
    )
    inference = inference_runner(
        artifact,
        store,
        clock_ns=clock_ns,
    )

    events = store.read_all_ingest_order()
    if not events:
        raise AssertionError("observation cycle completed without persisted events")
    if not store.verify_chain():
        raise RuntimeError("Event Store hash chain failed after observed cycle")

    return ObservedShadowCycleResult(
        binance=binance,
        protocol=protocol,
        inference=inference,
        store_tip_hash=events[-1].event_hash,
        store_event_count=len(events),
    )
