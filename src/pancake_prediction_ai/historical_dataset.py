from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .dataset import TrainingExample
from .event_store import EventStore
from .historical_onchain import reconstruct_protocol_snapshot
from .onchain_collector import collect_protocol_snapshot_at_anchor
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT
from .portable_features import PortableFeaturePolicy, build_portable_features
from .read_only_rpc import ReadOnlyJsonRpcClient
from .replay import build_snapshot
from .round_history import RoundTimeline
from .rpc_snapshot import find_block_at_or_before_timestamp


@dataclass(frozen=True, slots=True)
class DecisionSnapshotPoint:
    epoch: int
    decision_cutoff_ns: int
    block_number: int
    block_timestamp_s: int
    reconstructed_observed_at_ns: int


@dataclass(frozen=True, slots=True)
class DecisionSnapshotBackfillResult:
    points: tuple[DecisionSnapshotPoint, ...]
    already_present_epochs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExampleSkip:
    epoch: int
    reason: str


@dataclass(frozen=True, slots=True)
class HistoricalExampleBuildResult:
    examples: tuple[TrainingExample, ...]
    skipped: tuple[ExampleSkip, ...]
    feature_names: tuple[str, ...]


def _decision_cutoff_ns(timeline: RoundTimeline, *, decision_lead_ns: int) -> int:
    if decision_lead_ns <= 0:
        raise ValueError("decision_lead_ns must be positive")
    cutoff = timeline.lock_timestamp_ns - decision_lead_ns
    if cutoff <= timeline.start_event.event_time_ns:
        raise ValueError(f"decision cutoff for epoch {timeline.epoch} is not after round start")
    return cutoff


def backfill_round_decision_snapshots(
    client: ReadOnlyJsonRpcClient,
    store: EventStore,
    timelines: Iterable[RoundTimeline],
    *,
    dataset_id: str,
    decision_lead_ns: int,
    assumed_onchain_latency_ns: int,
    lower_block: int,
    upper_block: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> DecisionSnapshotBackfillResult:
    """Reconstruct the latest protocol snapshot actually available by decision time.

    Block selection subtracts the assumed on-chain observation latency *before*
    searching for a block. Consequently the reconstructed snapshot availability
    (`block timestamp + latency`) cannot fall after the requested decision cutoff.
    """

    if store.mode != "reconstructed":
        raise ValueError("decision snapshot backfill requires reconstructed Event Store")
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if decision_lead_ns <= 0:
        raise ValueError("decision_lead_ns must be positive")
    if assumed_onchain_latency_ns < 0:
        raise ValueError("assumed_onchain_latency_ns must be non-negative")
    if lower_block < 0 or upper_block < lower_block:
        raise ValueError("invalid block bounds")
    if client.chain_id() != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}")

    existing_event_ids = {
        stored.event.event_id for stored in store.read_all_ingest_order()
    }
    points: list[DecisionSnapshotPoint] = []
    already_present: list[int] = []

    for timeline in timelines:
        cutoff_ns = _decision_cutoff_ns(timeline, decision_lead_ns=decision_lead_ns)
        latest_block_time_ns = cutoff_ns - assumed_onchain_latency_ns
        if latest_block_time_ns <= 0:
            raise ValueError(f"latency pushes epoch {timeline.epoch} before positive timestamp")
        anchor = find_block_at_or_before_timestamp(
            client,
            target_timestamp_s=latest_block_time_ns // 1_000_000_000,
            lower_block=lower_block,
            upper_block=upper_block,
        )

        expected_round_event_id = (
            f"reconstructed:{dataset_id}:pancake:prediction:round_snapshot:"
            f"{prediction_contract.lower()}:{timeline.epoch}:{anchor.number}"
        )
        if expected_round_event_id in existing_event_ids:
            already_present.append(timeline.epoch)
            continue

        source = collect_protocol_snapshot_at_anchor(
            client,
            anchor=anchor,
            prediction_contract=prediction_contract,
        )
        if source.current_epoch != timeline.epoch:
            raise ValueError(
                f"historical currentEpoch mismatch at decision snapshot: "
                f"timeline={timeline.epoch} chain={source.current_epoch} block={anchor.number}"
            )
        reconstructed = reconstruct_protocol_snapshot(
            source,
            dataset_id=dataset_id,
            assumed_latency_ns=assumed_onchain_latency_ns,
        )
        reconstructed_observed_at_ns = anchor.timestamp_s * 1_000_000_000 + assumed_onchain_latency_ns
        if reconstructed_observed_at_ns > cutoff_ns:
            raise AssertionError("selected protocol snapshot is unavailable by decision cutoff")
        stored = store.append_many(reconstructed)
        existing_event_ids.update(item.event.event_id for item in stored)
        points.append(
            DecisionSnapshotPoint(
                epoch=timeline.epoch,
                decision_cutoff_ns=cutoff_ns,
                block_number=anchor.number,
                block_timestamp_s=anchor.timestamp_s,
                reconstructed_observed_at_ns=reconstructed_observed_at_ns,
            )
        )

    return DecisionSnapshotBackfillResult(
        points=tuple(points),
        already_present_epochs=tuple(already_present),
    )


def _latest_round_epoch_before_cutoff(store_events, cutoff_ns: int) -> int | None:
    snapshot = build_snapshot(store_events, cutoff_ns=cutoff_ns)
    items = snapshot.by_source_topic("pancake_prediction", "prediction.round_snapshot")
    if not items:
        return None
    value = items[-1].event.payload.get("epoch")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("round snapshot epoch must be integer")
    return value


def build_portable_round_examples(
    store: EventStore,
    timelines: Iterable[RoundTimeline],
    *,
    decision_lead_ns: int,
    feature_policy: PortableFeaturePolicy = PortableFeaturePolicy(),
) -> HistoricalExampleBuildResult:
    """Build leakage-safe round examples from a reconstructed Event Store."""

    if store.mode != "reconstructed":
        raise ValueError("historical examples require reconstructed Event Store")
    events = store.read_all_ingest_order()
    examples: list[TrainingExample] = []
    skipped: list[ExampleSkip] = []
    feature_names: tuple[str, ...] | None = None

    for timeline in timelines:
        cutoff_ns = _decision_cutoff_ns(timeline, decision_lead_ns=decision_lead_ns)
        if timeline.label_available_at_ns <= cutoff_ns:
            raise ValueError(f"epoch {timeline.epoch} label is available before decision cutoff")
        latest_epoch = _latest_round_epoch_before_cutoff(events, cutoff_ns)
        if latest_epoch is None:
            skipped.append(ExampleSkip(timeline.epoch, "missing_protocol_snapshot"))
            continue
        if latest_epoch != timeline.epoch:
            skipped.append(
                ExampleSkip(
                    timeline.epoch,
                    f"protocol_snapshot_epoch_mismatch:{latest_epoch}",
                )
            )
            continue

        snapshot = build_snapshot(events, cutoff_ns=cutoff_ns)
        try:
            features = build_portable_features(snapshot, policy=feature_policy)
        except ValueError as exc:
            skipped.append(ExampleSkip(timeline.epoch, f"feature_unavailable:{exc}"))
            continue
        feature_dict = features.as_dict()
        names = tuple(feature_dict)
        if feature_names is None:
            feature_names = names
        elif names != feature_names:
            raise AssertionError("portable feature schema changed across examples")
        examples.append(
            TrainingExample(
                round_id=timeline.epoch,
                decision_cutoff_ns=cutoff_ns,
                label_available_at_ns=timeline.label_available_at_ns,
                features=tuple(feature_dict.items()),
                outcome=timeline.outcome,
            )
        )

    return HistoricalExampleBuildResult(
        examples=tuple(examples),
        skipped=tuple(skipped),
        feature_names=feature_names or (),
    )
