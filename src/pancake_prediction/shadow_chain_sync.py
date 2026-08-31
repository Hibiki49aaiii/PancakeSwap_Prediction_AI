from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .collector import ReadOnlyRpc
from .contracts import Market
from .public_collector import PublicHistoricalCollector
from .rpc import RpcError
from .store import EventStore


class ShadowChainSourceIntegrityError(RpcError):
    pass


class ShadowChainSyncRpc(ReadOnlyRpc, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class ShadowChainSyncReport:
    market: str
    database: str
    head_block: int
    safe_head_block: int
    previous_last_collected_block: int
    from_block: int | None
    to_block: int | None
    confirmations: int
    prediction_events_inserted: int
    chainlink_events_inserted: int
    reorg_blocks_detected: tuple[int, ...]
    oracle_proxy: str
    chainlink_aggregator: str
    oracle_stability_proof: dict[str, object]
    no_new_confirmed_blocks: bool

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["reorg_blocks_detected"] = list(self.reorg_blocks_detected)
        return payload


def _metadata_address(
    store: EventStore,
    key: str,
    *,
    label: str,
) -> str:
    value = store.metadata(key)
    if value is None:
        raise ValueError(
            f"canonical database is missing {label}; "
            "run a recent bootstrap with Chainlink route proof first"
        )
    normalized = value.lower()
    if (
        not normalized.startswith("0x")
        or len(normalized) != 42
        or any(char not in "0123456789abcdef" for char in normalized[2:])
    ):
        raise ValueError(f"canonical database {label} is invalid")
    return normalized


def _metadata_block(store: EventStore, key: str, *, label: str) -> int:
    value = store.metadata(key)
    if value is None:
        raise ValueError(
            f"canonical database is missing {label}; "
            "run a recent bootstrap before live shadow sync"
        )
    try:
        block = int(value)
    except ValueError as exc:
        raise ValueError(f"canonical database {label} is invalid") from exc
    if block < 0:
        raise ValueError(f"canonical database {label} must be non-negative")
    return block


def sync_shadow_chain(
    rpc: ShadowChainSyncRpc,
    market: Market,
    database: Path,
    *,
    confirmations: int = 3,
    chunk_size: int = 2_000,
    reorg_lookback: int = 64,
) -> ShadowChainSyncReport:
    if confirmations < 0:
        raise ValueError("confirmations must be non-negative")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if reorg_lookback < 1:
        raise ValueError("reorg_lookback must be positive")

    store = EventStore(database)
    store.initialize()
    previous_last = _metadata_block(
        store,
        f"{market.symbol}.last_collected_block",
        label="last_collected_block",
    )
    anchored_aggregator = _metadata_address(
        store,
        f"{market.symbol}.oracle_anchor_address",
        label="oracle_anchor_address",
    )
    anchored_proxy = _metadata_address(
        store,
        f"{market.symbol}.oracle_proxy_anchor_address",
        label="oracle_proxy_anchor_address",
    )

    head = rpc.block_number()
    safe_head = head - confirmations
    if safe_head < 0:
        raise ValueError("confirmed BSC head is negative")

    collector = PublicHistoricalCollector(
        rpc=rpc,
        store=store,
        chunk_size=chunk_size,
        reorg_lookback=reorg_lookback,
    )

    if safe_head <= previous_last:
        proof = collector.prove_latest_oracle_stable_since(
            market,
            from_block=max(0, previous_last - reorg_lookback + 1),
            through_block=previous_last,
        )
        oracle_proxy = str(proof.get("oracle", "")).lower()
        aggregator = str(proof.get("chainlink_aggregator", "")).lower()
        if oracle_proxy != anchored_proxy or aggregator != anchored_aggregator:
            raise ShadowChainSourceIntegrityError(
                "live oracle route differs from canonical route anchor; "
                "start a new source-bound campaign"
            )
        return ShadowChainSyncReport(
            market=market.symbol,
            database=str(database),
            head_block=head,
            safe_head_block=safe_head,
            previous_last_collected_block=previous_last,
            from_block=None,
            to_block=None,
            confirmations=confirmations,
            prediction_events_inserted=0,
            chainlink_events_inserted=0,
            reorg_blocks_detected=(),
            oracle_proxy=oracle_proxy,
            chainlink_aggregator=aggregator,
            oracle_stability_proof=proof,
            no_new_confirmed_blocks=True,
        )

    from_block = max(0, previous_last - reorg_lookback + 1)
    proof = collector.prove_latest_oracle_stable_since(
        market,
        from_block=from_block,
        through_block=safe_head,
    )
    oracle_proxy = str(proof.get("oracle", "")).lower()
    aggregator = str(proof.get("chainlink_aggregator", "")).lower()
    if oracle_proxy != anchored_proxy or aggregator != anchored_aggregator:
        raise ShadowChainSourceIntegrityError(
            "live oracle route differs from canonical route anchor; "
            "start a new source-bound campaign"
        )

    prediction_report = collector.collect_market(
        market,
        from_block,
        safe_head,
        include_chainlink=False,
        prediction_analytic_only=False,
    )
    chainlink_report = collector.collect_chainlink_feed(
        market,
        aggregator_address=aggregator,
        from_block=from_block,
        to_block=safe_head,
    )

    prediction_count = prediction_report.get("prediction_events_inserted")
    chainlink_count = chainlink_report.get("chainlink_events_inserted")
    reorgs = prediction_report.get("reorg_blocks_detected")
    if not isinstance(prediction_count, int) or not isinstance(chainlink_count, int):
        raise RuntimeError("live collector reports are missing event counts")
    if not isinstance(reorgs, list) or any(not isinstance(item, int) for item in reorgs):
        raise RuntimeError("live collector report has invalid reorg block list")

    return ShadowChainSyncReport(
        market=market.symbol,
        database=str(database),
        head_block=head,
        safe_head_block=safe_head,
        previous_last_collected_block=previous_last,
        from_block=from_block,
        to_block=safe_head,
        confirmations=confirmations,
        prediction_events_inserted=prediction_count,
        chainlink_events_inserted=chainlink_count,
        reorg_blocks_detected=tuple(reorgs),
        oracle_proxy=oracle_proxy,
        chainlink_aggregator=aggregator,
        oracle_stability_proof=proof,
        no_new_confirmed_blocks=False,
    )
