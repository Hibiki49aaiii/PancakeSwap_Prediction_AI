from __future__ import annotations

from dataclasses import dataclass

from .binance_public_rest import BinancePublicRestClient
from .event_store import EventStore
from .historical_binance import HistoricalBinanceBackfillResult
from .historical_dataset import DecisionSnapshotBackfillResult, HistoricalExampleBuildResult
from .historical_pipeline import HistoricalPipeline, HistoricalPipelineConfig
from .read_only_rpc import ReadOnlyJsonRpcClient
from .round_history import LifecycleBackfillResult, RoundTimeline


@dataclass(frozen=True, slots=True)
class HistoricalBinanceWindow:
    start_time_ms: int
    end_time_ms: int
    epochs: tuple[int, ...]

    def validate(self) -> None:
        if self.start_time_ms < 0 or self.end_time_ms <= self.start_time_ms:
            raise ValueError("historical Binance window is invalid")
        if not self.epochs:
            raise ValueError("historical Binance window must reference at least one epoch")


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceRunResult:
    dataset_id: str
    lifecycle: LifecycleBackfillResult
    completed_rounds: int
    incomplete_epochs: tuple[int, ...]
    binance_windows: tuple[HistoricalBinanceWindow, ...]
    binance_results: tuple[HistoricalBinanceBackfillResult, ...]
    decision_snapshots: DecisionSnapshotBackfillResult
    examples: HistoricalExampleBuildResult
    store_event_count: int
    store_tip_hash: str


def _decision_cutoff_ns(timeline: RoundTimeline, config: HistoricalPipelineConfig) -> int:
    cutoff = timeline.lock_timestamp_ns - config.decision_lead_ns
    if cutoff <= timeline.start_event.event_time_ns:
        raise ValueError(f"decision cutoff for epoch {timeline.epoch} is not after round start")
    return cutoff


def build_historical_binance_windows(
    timelines: tuple[RoundTimeline, ...],
    config: HistoricalPipelineConfig,
) -> tuple[HistoricalBinanceWindow, ...]:
    """Return only the Binance intervals required by the portable feature windows.

    Historical aggregate trades are reconstructed with an availability latency,
    but the model's trade windows are defined on exchange event time. Therefore
    each requested interval covers `[decision_cutoff - long_window, cutoff]`;
    replay later excludes trades whose reconstructed observation time falls after
    the cutoff. Adjacent/overlapping intervals are merged to avoid duplicate REST
    work without turning a sparse round sample into a full multi-hour backfill.
    """

    config.validate()
    if not timelines:
        return ()

    raw: list[HistoricalBinanceWindow] = []
    for timeline in timelines:
        cutoff_ns = _decision_cutoff_ns(timeline, config)
        start_ns = cutoff_ns - config.feature_policy.long_window_ns
        if start_ns < 0:
            raise ValueError(f"feature window for epoch {timeline.epoch} starts before zero")
        start_ms = start_ns // 1_000_000
        end_ms = (cutoff_ns + 999_999) // 1_000_000
        if end_ms <= start_ms:
            end_ms = start_ms + 1
        raw.append(HistoricalBinanceWindow(start_ms, end_ms, (timeline.epoch,)))

    raw.sort(key=lambda item: (item.start_time_ms, item.end_time_ms, item.epochs))
    merged: list[HistoricalBinanceWindow] = []
    for window in raw:
        window.validate()
        if not merged or window.start_time_ms > merged[-1].end_time_ms + 1:
            merged.append(window)
            continue
        previous = merged[-1]
        merged[-1] = HistoricalBinanceWindow(
            start_time_ms=previous.start_time_ms,
            end_time_ms=max(previous.end_time_ms, window.end_time_ms),
            epochs=previous.epochs + window.epochs,
        )

    for window in merged:
        window.validate()
    return tuple(merged)


def run_historical_evidence_acquisition(
    store: EventStore,
    *,
    config: HistoricalPipelineConfig,
    binance_client: BinancePublicRestClient,
    rpc_client: ReadOnlyJsonRpcClient,
    from_block: int,
    to_block: int,
    symbol: str = "BNBUSDT",
    lifecycle_chunk_size: int = 5_000,
    binance_batch_limit: int = 1_000,
    binance_max_batches_per_window: int = 10_000,
) -> HistoricalEvidenceRunResult:
    """Populate one reconstructed research store using a reproducible sparse run.

    The run remains strictly reconstructed evidence. It first obtains round
    lifecycle labels, derives the exact Binance feature windows for completed
    rounds, reconstructs decision-time Pancake/Chainlink snapshots, and finally
    attempts leakage-safe example construction. No signing or transaction path
    exists in this orchestration.
    """

    if store.mode != "reconstructed":
        raise ValueError("historical evidence acquisition requires reconstructed Event Store")
    config.validate()
    if from_block < 0 or to_block < from_block:
        raise ValueError("historical evidence block range is invalid")
    if lifecycle_chunk_size <= 0:
        raise ValueError("lifecycle_chunk_size must be positive")
    if not 1 <= binance_batch_limit <= 1_000:
        raise ValueError("binance_batch_limit must be in [1, 1000]")
    if binance_max_batches_per_window <= 0:
        raise ValueError("binance_max_batches_per_window must be positive")
    if not store.verify_chain():
        raise ValueError("source Event Store hash chain verification failed before acquisition")

    pipeline = HistoricalPipeline(store, config)
    lifecycle = pipeline.backfill_lifecycle(
        rpc_client,
        from_block=from_block,
        to_block=to_block,
        chunk_size=lifecycle_chunk_size,
    )
    timelines = pipeline.timelines()
    if not timelines.completed:
        raise ValueError("historical evidence range contains no completed Prediction rounds")

    windows = build_historical_binance_windows(timelines.completed, config)
    binance_results: list[HistoricalBinanceBackfillResult] = []
    for window in windows:
        binance_results.append(
            pipeline.backfill_binance(
                binance_client,
                symbol=symbol,
                start_time_ms=window.start_time_ms,
                end_time_ms=window.end_time_ms,
                batch_limit=binance_batch_limit,
                max_batches=binance_max_batches_per_window,
            )
        )

    decision_snapshots = pipeline.backfill_decision_protocol(
        rpc_client,
        lower_block=from_block,
        upper_block=to_block,
    )
    examples = pipeline.build_examples()

    events = store.read_all_ingest_order()
    if not events:
        raise AssertionError("historical evidence acquisition produced an empty Event Store")
    if not store.verify_chain():
        raise ValueError("Event Store hash chain verification failed after acquisition")

    return HistoricalEvidenceRunResult(
        dataset_id=config.dataset_id,
        lifecycle=lifecycle,
        completed_rounds=len(timelines.completed),
        incomplete_epochs=timelines.incomplete_epochs,
        binance_windows=windows,
        binance_results=tuple(binance_results),
        decision_snapshots=decision_snapshots,
        examples=examples,
        store_event_count=len(events),
        store_tip_hash=events[-1].event_hash,
    )
