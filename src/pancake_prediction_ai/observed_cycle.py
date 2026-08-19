from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .binance_ingest import BinancePollResult, poll_binance_public_once
from .binance_public_rest import BinancePublicRestClient
from .event_store import EventStore
from .onchain_ingest import ProtocolIngestResult, collect_and_persist_protocol_snapshot
from .read_only_rpc import ReadOnlyJsonRpcClient
from .shadow_economics import (
    ShadowEconomicDecisionResult,
    ShadowEconomicPolicy,
    record_shadow_economic_decision,
)
from .shadow_inference import ShadowInferenceResult, infer_shadow_decision
from .trained_model_artifact import PromotedModelArtifact


ClockNs = Callable[[], int]
BinanceCollector = Callable[..., BinancePollResult]
ProtocolCollector = Callable[..., ProtocolIngestResult]
InferenceRunner = Callable[..., ShadowInferenceResult]
EconomicRunner = Callable[..., ShadowEconomicDecisionResult]


@dataclass(frozen=True, slots=True)
class ObservedShadowCycleResult:
    binance: BinancePollResult
    protocol: ProtocolIngestResult
    inference: ShadowInferenceResult
    economic: ShadowEconomicDecisionResult | None
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
    economic_policy: ShadowEconomicPolicy | None = None,
    binance_collector: BinanceCollector = poll_binance_public_once,
    protocol_collector: ProtocolCollector = collect_and_persist_protocol_snapshot,
    inference_runner: InferenceRunner = infer_shadow_decision,
    economic_runner: EconomicRunner = record_shadow_economic_decision,
) -> ObservedShadowCycleResult:
    """Run one read-only observation -> inference -> optional simulated-EV cycle.

    The cycle only consumes public Binance market data and allowlisted read-only
    BSC JSON-RPC calls. Every observation is persisted to the same append-only
    Event Store before inference. The model decision is therefore bound to a
    concrete hash-chain tip rather than to transient in-memory data.

    When an economic policy is supplied, an accepted model inference is followed
    by a simulated BULL/BEAR EV comparison that uses the exact Pancake pool at
    the model source tip. This remains paper/shadow accounting; no transaction
    construction, signing, or broadcasting is performed.
    """

    if store.mode != "observed":
        raise ValueError("observed shadow cycle requires observed Event Store")
    if not symbol.strip():
        raise ValueError("symbol is required")
    if not 1 <= trade_limit <= 1000:
        raise ValueError("trade_limit must be in [1, 1000]")
    artifact.validate()
    if economic_policy is not None:
        economic_policy.validate()

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
    economic = None
    if inference.accepted and economic_policy is not None:
        economic = economic_runner(
            inference,
            store,
            policy=economic_policy,
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
        economic=economic,
        store_tip_hash=events[-1].event_hash,
        store_event_count=len(events),
    )
