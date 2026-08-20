from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .collector import ReadOnlyRpc
from .contracts import Market
from .public_collector import PublicHistoricalCollector
from .quality import QualityReport, build_quality_report
from .replay import ReplaySnapshot, build_replay_snapshot
from .store import EventStore


class RecentBootstrapRpc(ReadOnlyRpc, Protocol):
    def block_number(self) -> int: ...
    def block(self, number: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class TimestampBlockRange:
    requested_start_timestamp: int
    requested_end_timestamp: int
    from_block: int
    to_block: int
    from_block_timestamp: int
    to_block_timestamp: int
    head_block: int
    confirmations: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecentBootstrapResult:
    market: str
    database: str
    block_range: TimestampBlockRange
    collection: dict[str, object]
    quality: QualityReport
    replay_rounds: int
    replay_input_digest: str
    replay_output_digest: str
    chainlink_collected: bool
    authoritative_prediction_events: bool
    oracle_stability_proof: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "database": self.database,
            "block_range": self.block_range.as_dict(),
            "collection": self.collection,
            "quality": self.quality.as_dict(),
            "replay_rounds": self.replay_rounds,
            "replay_input_digest": self.replay_input_digest,
            "replay_output_digest": self.replay_output_digest,
            "chainlink_collected": self.chainlink_collected,
            "authoritative_prediction_events": self.authoritative_prediction_events,
            "oracle_stability_proof": self.oracle_stability_proof,
        }


def _block_timestamp(rpc: RecentBootstrapRpc, block_number: int) -> int:
    block = rpc.block(block_number)
    raw = block.get("timestamp")
    if not isinstance(raw, str):
        raise ValueError(f"block {block_number} is missing a hex timestamp")
    try:
        return int(raw, 16)
    except ValueError as exc:
        raise ValueError(f"block {block_number} has an invalid timestamp") from exc


def first_block_at_or_after(
    rpc: RecentBootstrapRpc,
    timestamp: int,
    *,
    upper_block: int,
    lower_block: int = 0,
) -> int:
    if timestamp < 0 or lower_block < 0 or upper_block < 0:
        raise ValueError("timestamp and block bounds must be non-negative")
    if lower_block > upper_block:
        raise ValueError("lower_block must not exceed upper_block")
    upper_timestamp = _block_timestamp(rpc, upper_block)
    if timestamp > upper_timestamp:
        raise ValueError("requested timestamp is later than the upper block")
    low = lower_block
    high = upper_block
    while low < high:
        mid = (low + high) // 2
        if _block_timestamp(rpc, mid) >= timestamp:
            high = mid
        else:
            low = mid + 1
    return low


def recent_search_lower_bound(
    rpc: RecentBootstrapRpc,
    timestamp: int,
    *,
    upper_block: int,
) -> int:
    """Find a nearby lower bound without probing arbitrary old block headers."""

    if timestamp < 0 or upper_block < 0:
        raise ValueError("timestamp and upper_block must be non-negative")
    upper_timestamp = _block_timestamp(rpc, upper_block)
    if timestamp > upper_timestamp:
        raise ValueError("requested timestamp is later than the upper block")
    if upper_block == 0:
        return 0

    distance = 1
    while True:
        candidate = max(0, upper_block - distance)
        candidate_timestamp = _block_timestamp(rpc, candidate)
        if candidate == 0 or candidate_timestamp < timestamp:
            return candidate
        distance *= 2


def resolve_timestamp_block_range(
    rpc: RecentBootstrapRpc,
    *,
    start_timestamp: int,
    end_timestamp: int,
    confirmations: int = 64,
) -> TimestampBlockRange:
    if start_timestamp < 0 or end_timestamp <= start_timestamp:
        raise ValueError("end_timestamp must be greater than non-negative start_timestamp")
    if confirmations < 0:
        raise ValueError("confirmations must be non-negative")
    head = rpc.block_number()
    safe_head = head - confirmations
    if safe_head < 0:
        raise ValueError("confirmed head is negative")
    safe_head_timestamp = _block_timestamp(rpc, safe_head)
    if end_timestamp > safe_head_timestamp:
        raise ValueError("requested end timestamp is later than the confirmed head")

    search_lower = recent_search_lower_bound(
        rpc,
        start_timestamp,
        upper_block=safe_head,
    )
    from_block = first_block_at_or_after(
        rpc,
        start_timestamp,
        lower_block=search_lower,
        upper_block=safe_head,
    )
    end_boundary = first_block_at_or_after(
        rpc,
        end_timestamp,
        lower_block=from_block,
        upper_block=safe_head,
    )
    to_block = max(from_block, end_boundary - 1)
    return TimestampBlockRange(
        requested_start_timestamp=start_timestamp,
        requested_end_timestamp=end_timestamp,
        from_block=from_block,
        to_block=to_block,
        from_block_timestamp=_block_timestamp(rpc, from_block),
        to_block_timestamp=_block_timestamp(rpc, to_block),
        head_block=head,
        confirmations=confirmations,
    )


def _record_recent_chainlink_anchor(
    store: EventStore,
    market: Market,
    *,
    from_block: int,
    oracle_proxy: str,
    chainlink_aggregator: str,
) -> None:
    """Anchor recent active-Chainlink history to the proven event emitter.

    ``Prediction.oracle()`` is a proxy address on the current BNBUSD contract,
    while ``AnswerUpdated`` is emitted by the proxy's underlying aggregator.
    ``oracle_history`` compares its anchor address to the event contract address,
    so the research anchor must be the proven aggregator, not the proxy.  The
    anchor block represents state immediately before the recent collection
    window, making the synthetic activation effective at ``from_block``.
    """

    if from_block <= 0:
        raise ValueError("recent Chainlink anchor requires a positive from_block")
    store.record_metadata(f"{market.symbol}.oracle_anchor_block", str(from_block - 1))
    store.record_metadata(
        f"{market.symbol}.oracle_anchor_address", chainlink_aggregator.lower()
    )
    store.record_metadata(
        f"{market.symbol}.oracle_proxy_anchor_address", oracle_proxy.lower()
    )


def run_recent_prediction_bootstrap(
    rpc: RecentBootstrapRpc,
    market: Market,
    database: Path,
    *,
    start_timestamp: int,
    end_timestamp: int,
    confirmations: int = 64,
    chunk_size: int = 2_000,
    include_chainlink: bool = False,
) -> RecentBootstrapResult:
    block_range = resolve_timestamp_block_range(
        rpc,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        confirmations=confirmations,
    )
    store = EventStore(database)
    store.initialize()

    oracle_stability_proof: dict[str, object] | None = None
    if include_chainlink:
        proof_collector = PublicHistoricalCollector(
            rpc=rpc,
            store=store,
            chunk_size=max(chunk_size, 50_000),
        )
        oracle_stability_proof = proof_collector.prove_oracle_stable_in_range(
            market,
            from_block=block_range.from_block,
            through_block=block_range.to_block,
        )

    collector = PublicHistoricalCollector(
        rpc=rpc,
        store=store,
        chunk_size=chunk_size,
    )
    collection = collector.collect_market(
        market,
        block_range.from_block,
        block_range.to_block,
        include_chainlink=False,
        prediction_analytic_only=False,
    )

    chainlink_collected = False
    if oracle_stability_proof is not None:
        aggregator = oracle_stability_proof.get("chainlink_aggregator")
        oracle_proxy = oracle_stability_proof.get("oracle")
        if not isinstance(aggregator, str) or not isinstance(oracle_proxy, str):
            raise RuntimeError("oracle stability proof is missing Chainlink route addresses")
        _record_recent_chainlink_anchor(
            store,
            market,
            from_block=block_range.from_block,
            oracle_proxy=oracle_proxy,
            chainlink_aggregator=aggregator,
        )
        chainlink_report = collector.collect_chainlink_feed(
            market,
            aggregator_address=aggregator,
            from_block=block_range.from_block,
            to_block=block_range.to_block,
        )
        chainlink_count = chainlink_report.get("chainlink_events_inserted")
        if not isinstance(chainlink_count, int):
            raise RuntimeError("Chainlink collector did not return an event count")
        collection = dict(collection)
        collection.update(
            {
                "include_chainlink": True,
                "chainlink_events_inserted": chainlink_count,
                "oracle_addresses": [oracle_proxy],
                "chainlink_event_addresses": [aggregator],
                "chainlink_collector_run_id": chainlink_report["collector_run_id"],
            }
        )
        chainlink_collected = chainlink_count > 0

    quality = build_quality_report(database, market.symbol)
    replay: ReplaySnapshot = build_replay_snapshot(database, market.symbol)
    return RecentBootstrapResult(
        market=market.symbol,
        database=str(database),
        block_range=block_range,
        collection=collection,
        quality=quality,
        replay_rounds=len(replay.rounds),
        replay_input_digest=replay.input_digest,
        replay_output_digest=replay.output_digest,
        chainlink_collected=chainlink_collected,
        authoritative_prediction_events=True,
        oracle_stability_proof=oracle_stability_proof,
    )