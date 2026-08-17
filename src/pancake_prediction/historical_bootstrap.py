from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .collector import HistoricalCollector, ReadOnlyRpc
from .contracts import Market
from .historical_preflight import (
    HistoricalPreflightResult,
    HistoricalPreflightRpc,
    run_historical_preflight,
)
from .quality import QualityReport, build_quality_report
from .replay import ReplaySnapshot, build_replay_snapshot
from .rpc import RpcError
from .store import EventStore


class HistoricalBootstrapRpc(HistoricalPreflightRpc, ReadOnlyRpc, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class CollectionRange:
    from_block: int
    to_block: int
    confirmations: int

    def as_dict(self) -> dict[str, int]:
        return {
            "from_block": self.from_block,
            "to_block": self.to_block,
            "confirmations": self.confirmations,
        }


@dataclass(frozen=True, slots=True)
class HistoricalBootstrapResult:
    market: str
    database: str
    preflight: HistoricalPreflightResult
    collection_range: CollectionRange
    collection: dict[str, object]
    quality: QualityReport
    replay_rounds: int
    replay_input_digest: str
    replay_output_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "database": self.database,
            "preflight": self.preflight.as_dict(),
            "collection_range": self.collection_range.as_dict(),
            "collection": self.collection,
            "quality": self.quality.as_dict(),
            "replay_rounds": self.replay_rounds,
            "replay_input_digest": self.replay_input_digest,
            "replay_output_digest": self.replay_output_digest,
        }


def resolve_collection_range(
    preflight: HistoricalPreflightResult,
    *,
    confirmations: int = 64,
    from_block: int | None = None,
    to_block: int | None = None,
) -> CollectionRange:
    if confirmations < 0:
        raise ValueError("confirmations must be non-negative")
    safe_head = preflight.head_block - confirmations
    if safe_head < preflight.deployment_block:
        raise RpcError("confirmed head is earlier than the deployment block")

    start = preflight.deployment_block if from_block is None else from_block
    end = safe_head if to_block is None else to_block
    if start < preflight.deployment_block:
        raise ValueError("from_block cannot be earlier than deployment")
    if end > safe_head:
        raise ValueError("to_block cannot exceed the confirmed head")
    if end < start:
        raise ValueError("collection range is empty or reversed")
    return CollectionRange(
        from_block=start,
        to_block=end,
        confirmations=confirmations,
    )


def _persist_oracle_anchor(
    store: EventStore,
    preflight: HistoricalPreflightResult,
) -> None:
    market = preflight.market
    anchor = preflight.archive_probe
    store.record_metadata(f"{market}.oracle_anchor_block", str(anchor.block_number))
    store.record_metadata(
        f"{market}.oracle_anchor_address",
        anchor.oracle_address.lower(),
    )


def run_historical_bootstrap(
    rpc: HistoricalBootstrapRpc,
    market: Market,
    database: Path,
    *,
    confirmations: int = 64,
    from_block: int | None = None,
    to_block: int | None = None,
    chunk_size: int = 2_000,
    include_chainlink: bool = True,
    prediction_analytic_only: bool = True,
) -> HistoricalBootstrapResult:
    preflight = run_historical_preflight(rpc, market)
    collection_range = resolve_collection_range(
        preflight,
        confirmations=confirmations,
        from_block=from_block,
        to_block=to_block,
    )
    store = EventStore(database)
    store.initialize()
    if include_chainlink:
        _persist_oracle_anchor(store, preflight)
    collector = HistoricalCollector(
        rpc=rpc,
        store=store,
        chunk_size=chunk_size,
    )
    collection = collector.collect_market(
        market,
        collection_range.from_block,
        collection_range.to_block,
        include_chainlink=include_chainlink,
        prediction_analytic_only=prediction_analytic_only,
    )
    quality = build_quality_report(database, market.symbol)
    replay: ReplaySnapshot = build_replay_snapshot(database, market.symbol)
    return HistoricalBootstrapResult(
        market=market.symbol,
        database=str(database),
        preflight=preflight,
        collection_range=collection_range,
        collection=collection,
        quality=quality,
        replay_rounds=len(replay.rounds),
        replay_input_digest=replay.input_digest,
        replay_output_digest=replay.output_digest,
    )
