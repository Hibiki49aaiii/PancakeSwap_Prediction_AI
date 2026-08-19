from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .binance_public_rest import BinancePublicRestClient
from .dataset_artifact import HistoricalDatasetArtifact, build_historical_dataset_artifact
from .event_store import EventStore
from .historical_binance import HistoricalBinanceBackfillResult, backfill_binance_aggregate_trades
from .historical_dataset import (
    DecisionSnapshotBackfillResult,
    HistoricalExampleBuildResult,
    backfill_round_decision_snapshots,
    build_portable_round_examples,
)
from .historical_store import bind_reconstruction_dataset, verify_reconstruction_dataset_binding
from .pancake_contract import BNB_PREDICTION_CONTRACT
from .portable_features import PortableFeaturePolicy
from .read_only_rpc import ReadOnlyJsonRpcClient
from .round_history import (
    LifecycleBackfillResult,
    RoundTimelineBuildResult,
    backfill_round_lifecycle_logs,
    build_round_timelines,
)


ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class HistoricalPipelineConfig:
    dataset_id: str
    decision_lead_ns: int
    assumed_binance_latency_ns: int
    assumed_onchain_latency_ns: int
    feature_policy: PortableFeaturePolicy = PortableFeaturePolicy()

    def validate(self) -> None:
        if not self.dataset_id:
            raise ValueError("dataset_id is required")
        if self.decision_lead_ns <= 0:
            raise ValueError("decision_lead_ns must be positive")
        if self.assumed_binance_latency_ns < 0:
            raise ValueError("assumed_binance_latency_ns must be non-negative")
        if self.assumed_onchain_latency_ns < 0:
            raise ValueError("assumed_onchain_latency_ns must be non-negative")
        self.feature_policy.validate()


class HistoricalPipeline:
    """High-level, namespace-locked entrypoint for reconstructed research.

    Binding occurs at construction time and persists as a SQLite INSERT trigger.
    Every stage therefore operates inside one reconstruction namespace instead
    of relying on callers to remember which dataset ID was used by each backfill.
    """

    def __init__(
        self,
        store: EventStore,
        config: HistoricalPipelineConfig,
        *,
        clock_ns: ClockNs = time.time_ns,
    ) -> None:
        config.validate()
        if store.mode != "reconstructed":
            raise ValueError("HistoricalPipeline requires reconstructed Event Store")
        bind_reconstruction_dataset(store, config.dataset_id)
        if not verify_reconstruction_dataset_binding(store):
            raise ValueError("reconstructed dataset namespace binding verification failed")
        self.store = store
        self.config = config
        self.clock_ns = clock_ns

    def backfill_binance(
        self,
        client: BinancePublicRestClient,
        *,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
        batch_limit: int = 1000,
        max_batches: int = 10_000,
    ) -> HistoricalBinanceBackfillResult:
        return backfill_binance_aggregate_trades(
            client,
            self.store,
            dataset_id=self.config.dataset_id,
            symbol=symbol,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            assumed_latency_ns=self.config.assumed_binance_latency_ns,
            batch_limit=batch_limit,
            max_batches=max_batches,
        )

    def backfill_lifecycle(
        self,
        client: ReadOnlyJsonRpcClient,
        *,
        from_block: int,
        to_block: int,
        chunk_size: int = 5_000,
        prediction_contract: str = BNB_PREDICTION_CONTRACT,
    ) -> LifecycleBackfillResult:
        return backfill_round_lifecycle_logs(
            client,
            self.store,
            dataset_id=self.config.dataset_id,
            from_block=from_block,
            to_block=to_block,
            assumed_latency_ns=self.config.assumed_onchain_latency_ns,
            chunk_size=chunk_size,
            prediction_contract=prediction_contract,
        )

    def timelines(self) -> RoundTimelineBuildResult:
        return build_round_timelines(
            stored.event for stored in self.store.read_all_ingest_order()
        )

    def backfill_decision_protocol(
        self,
        client: ReadOnlyJsonRpcClient,
        *,
        lower_block: int,
        upper_block: int,
        prediction_contract: str = BNB_PREDICTION_CONTRACT,
    ) -> DecisionSnapshotBackfillResult:
        timelines = self.timelines()
        if not timelines.completed:
            raise ValueError("no completed round timelines are available")
        return backfill_round_decision_snapshots(
            client,
            self.store,
            timelines.completed,
            dataset_id=self.config.dataset_id,
            decision_lead_ns=self.config.decision_lead_ns,
            assumed_onchain_latency_ns=self.config.assumed_onchain_latency_ns,
            lower_block=lower_block,
            upper_block=upper_block,
            prediction_contract=prediction_contract,
        )

    def build_examples(self) -> HistoricalExampleBuildResult:
        timelines = self.timelines()
        if not timelines.completed:
            raise ValueError("no completed round timelines are available")
        return build_portable_round_examples(
            self.store,
            timelines.completed,
            decision_lead_ns=self.config.decision_lead_ns,
            feature_policy=self.config.feature_policy,
        )

    def build_dataset_artifact(
        self,
        *,
        generated_at_ns: int | None = None,
    ) -> HistoricalDatasetArtifact:
        if not verify_reconstruction_dataset_binding(self.store):
            raise ValueError("reconstructed dataset namespace binding is not intact")
        result = self.build_examples()
        return build_historical_dataset_artifact(
            self.store,
            result,
            dataset_id=self.config.dataset_id,
            generated_at_ns=self.clock_ns() if generated_at_ns is None else generated_at_ns,
            decision_lead_ns=self.config.decision_lead_ns,
            assumed_binance_latency_ns=self.config.assumed_binance_latency_ns,
            assumed_onchain_latency_ns=self.config.assumed_onchain_latency_ns,
            feature_policy=self.config.feature_policy,
        )
