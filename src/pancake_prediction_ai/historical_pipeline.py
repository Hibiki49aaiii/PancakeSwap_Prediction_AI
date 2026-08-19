from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from .binance_public_rest import BinancePublicRestClient
from .dataset_artifact import HistoricalDatasetArtifact, build_historical_dataset_artifact
from .event_store import EventRecord, EventStore
from .historical_binance import HistoricalBinanceBackfillResult, backfill_binance_aggregate_trades
from .historical_dataset import (
    DecisionSnapshotBackfillResult,
    HistoricalExampleBuildResult,
    backfill_round_decision_snapshots,
    build_portable_round_examples,
)
from .historical_store import (
    bind_reconstruction_dataset,
    bind_reconstruction_prediction_interval_seconds,
    bind_reconstruction_subgraph_latency_ns,
    reconstruction_prediction_interval_seconds,
    reconstruction_subgraph_latency_ns,
    verify_reconstruction_dataset_binding,
)
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


def _prediction_interval_from_round_snapshots(events: tuple[EventRecord, ...]) -> int | None:
    """Recover the protocol-scheduled interval from reconstructed round state.

    `rounds(epoch).lockTimestamp` is fixed when the round starts. It is therefore
    safe to use for the decision clock, unlike the later LockRound transaction
    timestamp which may include operator delay. Multiple distinct intervals in
    one store are rejected because a single replay policy cannot silently span
    configuration regimes.
    """

    intervals: set[int] = set()
    for event in events:
        if event.source != "pancake_prediction" or event.topic != "prediction.round_snapshot":
            continue
        start = event.payload.get("start_timestamp")
        lock = event.payload.get("lock_timestamp")
        if isinstance(start, bool) or isinstance(lock, bool):
            raise ValueError("round snapshot timestamps must be integers")
        if not isinstance(start, int) or not isinstance(lock, int):
            raise ValueError("round snapshot timestamps must be integers")
        interval = lock - start
        if interval <= 0:
            raise ValueError("round snapshot scheduled lock interval must be positive")
        intervals.add(interval)
    if not intervals:
        return None
    if len(intervals) != 1:
        raise ValueError(
            f"reconstructed store spans multiple Prediction intervals: {sorted(intervals)}"
        )
    return next(iter(intervals))


@dataclass(frozen=True, slots=True)
class HistoricalPipelineConfig:
    dataset_id: str
    decision_lead_ns: int
    assumed_binance_latency_ns: int
    assumed_onchain_latency_ns: int
    feature_policy: PortableFeaturePolicy = PortableFeaturePolicy()
    prediction_interval_seconds: int | None = None
    assumed_subgraph_latency_ns: int | None = None

    def validate(self) -> None:
        if not self.dataset_id:
            raise ValueError("dataset_id is required")
        if self.decision_lead_ns <= 0:
            raise ValueError("decision_lead_ns must be positive")
        if self.assumed_binance_latency_ns < 0:
            raise ValueError("assumed_binance_latency_ns must be non-negative")
        if self.assumed_onchain_latency_ns < 0:
            raise ValueError("assumed_onchain_latency_ns must be non-negative")
        if self.prediction_interval_seconds is not None and self.prediction_interval_seconds <= 0:
            raise ValueError("prediction_interval_seconds must be positive when supplied")
        if self.assumed_subgraph_latency_ns is not None and self.assumed_subgraph_latency_ns < 0:
            raise ValueError("assumed_subgraph_latency_ns must be non-negative when supplied")
        self.feature_policy.validate()


class HistoricalPipeline:
    """High-level, namespace-locked entrypoint for reconstructed research.

    Dataset identity, scheduled Prediction interval, and optional subgraph
    availability latency persist in SQLite so later artifact generation cannot
    silently reinterpret the original replay assumptions.
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

        effective_config = config

        persisted_interval = reconstruction_prediction_interval_seconds(store)
        if config.prediction_interval_seconds is not None:
            bind_reconstruction_prediction_interval_seconds(
                store,
                config.prediction_interval_seconds,
            )
            persisted_interval = config.prediction_interval_seconds
        if config.prediction_interval_seconds is None and persisted_interval is not None:
            effective_config = replace(
                effective_config,
                prediction_interval_seconds=persisted_interval,
            )

        persisted_subgraph_latency = reconstruction_subgraph_latency_ns(store)
        if config.assumed_subgraph_latency_ns is not None:
            bind_reconstruction_subgraph_latency_ns(
                store,
                config.assumed_subgraph_latency_ns,
            )
            persisted_subgraph_latency = config.assumed_subgraph_latency_ns
        if config.assumed_subgraph_latency_ns is None and persisted_subgraph_latency is not None:
            effective_config = replace(
                effective_config,
                assumed_subgraph_latency_ns=persisted_subgraph_latency,
            )

        self.store = store
        self.config = effective_config
        self.clock_ns = clock_ns

    def _ensure_prediction_interval(self, events: tuple[EventRecord, ...]) -> int | None:
        interval = self.config.prediction_interval_seconds
        if interval is not None:
            return interval
        persisted = reconstruction_prediction_interval_seconds(self.store)
        if persisted is not None:
            self.config = replace(self.config, prediction_interval_seconds=persisted)
            return persisted
        derived = _prediction_interval_from_round_snapshots(events)
        if derived is not None:
            bind_reconstruction_prediction_interval_seconds(self.store, derived)
            self.config = replace(self.config, prediction_interval_seconds=derived)
        return derived

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
        events = tuple(stored.event for stored in self.store.read_all_ingest_order())
        interval = self._ensure_prediction_interval(events)
        return build_round_timelines(
            events,
            interval_seconds=interval,
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
            prediction_interval_seconds=self.config.prediction_interval_seconds,
            subgraph_availability_latency_ns=self.config.assumed_subgraph_latency_ns,
        )
