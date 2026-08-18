from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .abi import CHAINLINK_EVENTS
from .collector import HistoricalCollector
from .contracts import Market
from .deployment_provenance import (
    PredictionDeploymentProvenance,
    decode_prediction_v2_creation_transaction,
)
from .explorer_logs import EtherscanV2LogsClient, HybridExplorerRpc
from .quality import QualityReport, build_quality_report
from .replay import ReplaySnapshot, build_replay_snapshot
from .store import EventStore


class HistoricalHeaderRpc(Protocol):
    def chain_id(self) -> int: ...
    def block_number(self) -> int: ...
    def block(self, number: int) -> dict[str, Any]: ...
    def get_code(self, address: str, block: int | str = "latest") -> str: ...
    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str: ...
    def transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class ExplorerHistoricalBootstrapResult:
    market: str
    database: str
    from_block: int
    to_block: int
    deployment: PredictionDeploymentProvenance
    prediction_collection: dict[str, object]
    chainlink_events_inserted: int
    oracle_addresses: tuple[str, ...]
    quality: QualityReport
    replay_rounds: int
    replay_input_digest: str
    replay_output_digest: str
    explorer_manifest: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "database": self.database,
            "from_block": self.from_block,
            "to_block": self.to_block,
            "deployment": self.deployment.as_dict(),
            "prediction_collection": self.prediction_collection,
            "chainlink_events_inserted": self.chainlink_events_inserted,
            "oracle_addresses": self.oracle_addresses,
            "quality": self.quality.as_dict(),
            "replay_rounds": self.replay_rounds,
            "replay_input_digest": self.replay_input_digest,
            "replay_output_digest": self.replay_output_digest,
            "explorer_manifest": self.explorer_manifest,
            "archive_state_required": False,
            "historical_eth_call_required": False,
            "canonical_block_hash_verification": True,
            "signing_enabled": False,
            "live_broadcast": False,
        }


def _stored_new_oracles(store: EventStore, market: Market) -> set[str]:
    addresses: set[str] = set()
    for decoded in store.canonical_decoded_events(
        market=market.symbol,
        source="prediction",
        event_name="NewOracle",
    ):
        oracle = decoded.get("oracle")
        if isinstance(oracle, str):
            addresses.add(oracle.lower())
    return addresses


def _collect_seeded_chainlink(
    collector: HistoricalCollector,
    store: EventStore,
    market: Market,
    *,
    initial_oracle: str,
    from_block: int,
    to_block: int,
) -> tuple[int, tuple[str, ...]]:
    chain_id = collector.validate_chain()
    oracle_addresses = _stored_new_oracles(store, market)
    oracle_addresses.add(initial_oracle.lower())
    inserted = 0
    for oracle in sorted(oracle_addresses):
        count, _ = collector._collect_address_logs(
            chain_id=chain_id,
            address=oracle,
            market=market.symbol,
            source="chainlink",
            specs=CHAINLINK_EVENTS,
            from_block=from_block,
            to_block=to_block,
        )
        inserted += count
    store.record_metadata(
        f"{market.symbol}.historical_oracle_state",
        "constructor_seed_plus_neworacle_logs",
    )
    return inserted, tuple(sorted(oracle_addresses))


def run_explorer_historical_bootstrap(
    canonical_rpc: HistoricalHeaderRpc,
    explorer: EtherscanV2LogsClient,
    market: Market,
    database: Path,
    *,
    from_block: int,
    to_block: int,
    include_chainlink: bool = True,
    chunk_size: int = 2_000,
) -> ExplorerHistoricalBootstrapResult:
    if from_block < 0 or to_block < from_block:
        raise ValueError("invalid explorer bootstrap block range")
    if market.creation_tx_hash is None or market.deployment_block_hint is None:
        raise ValueError(f"{market.symbol} lacks verified creation metadata")
    if from_block < market.deployment_block_hint:
        raise ValueError("explorer bootstrap cannot begin before contract deployment")

    transaction = canonical_rpc.transaction_by_hash(market.creation_tx_hash)
    if transaction is None:
        raise ValueError("verified creation transaction is unavailable from canonical RPC")
    deployment = decode_prediction_v2_creation_transaction(transaction, market)

    store = EventStore(database)
    store.initialize()
    hybrid = HybridExplorerRpc(canonical_rpc=canonical_rpc, explorer_logs=explorer)
    collector = HistoricalCollector(
        rpc=hybrid,
        store=store,
        chunk_size=chunk_size,
    )
    prediction_collection = collector.collect_market(
        market,
        from_block,
        to_block,
        include_chainlink=False,
        prediction_analytic_only=False,
    )

    chainlink_count = 0
    oracle_addresses: tuple[str, ...] = ()
    if include_chainlink:
        chainlink_count, oracle_addresses = _collect_seeded_chainlink(
            collector,
            store,
            market,
            initial_oracle=deployment.oracle_address,
            from_block=from_block,
            to_block=to_block,
        )

    quality = build_quality_report(database, market.symbol)
    replay: ReplaySnapshot = build_replay_snapshot(database, market.symbol)
    return ExplorerHistoricalBootstrapResult(
        market=market.symbol,
        database=str(database),
        from_block=from_block,
        to_block=to_block,
        deployment=deployment,
        prediction_collection=prediction_collection,
        chainlink_events_inserted=chainlink_count,
        oracle_addresses=oracle_addresses,
        quality=quality,
        replay_rounds=len(replay.rounds),
        replay_input_digest=replay.input_digest,
        replay_output_digest=replay.output_digest,
        explorer_manifest=explorer.evidence_manifest(),
    )
