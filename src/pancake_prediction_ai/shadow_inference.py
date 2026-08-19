from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .event_store import EventRecord, EventStore, StoredEvent
from .portable_features import PortableFeaturePolicy, PortableFeatures, build_portable_features
from .portable_quality import PortableQualityPolicy, PortableQualityReport, assess_portable_quality
from .replay import build_snapshot
from .trained_model_artifact import PromotedModelArtifact


ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ShadowInferenceResult:
    accepted: bool
    blockers: tuple[str, ...]
    round_id: int | None
    probability_bull: float | None
    probability_bear: float | None
    probability_tie: float | None
    predicted_outcome: str | None
    promoted_model_artifact_sha256: str
    source_snapshot_tip_hash: str | None
    source_snapshot_digest: str | None
    source_snapshot_event_count: int
    stored_decision: StoredEvent | None


@dataclass(frozen=True, slots=True)
class _PreparedDecision:
    round_id: int
    features: PortableFeatures
    quality: PortableQualityReport
    probability_bull: float
    probability_bear: float
    probability_tie: float
    predicted_outcome: str
    source_tip_hash: str
    snapshot_digest: str
    source_event_count: int
    protocol_block_number: int


def _read_all_locked(store: EventStore) -> tuple[StoredEvent, ...]:
    rows = store._conn.execute(
        """
        SELECT ingest_seq, event_id, source, topic, event_time_ns,
               observed_at_ns, payload_json, prev_hash, event_hash
        FROM events ORDER BY ingest_seq ASC
        """
    ).fetchall()
    return tuple(store._decode_row(row) for row in rows)


def _snapshot_digest(events: tuple[StoredEvent, ...]) -> str:
    payload = [
        [event.ingest_seq, event.event_hash]
        for event in events
    ]
    canonical = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(canonical).hexdigest()


def _latest_round_id(events: tuple[StoredEvent, ...]) -> int | None:
    for stored in reversed(events):
        event = stored.event
        if event.source != "pancake_prediction" or event.topic != "prediction.round_snapshot":
            continue
        value = event.payload.get("epoch")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("latest round snapshot epoch is invalid")
        return value
    return None


def _round_already_decided(events: tuple[StoredEvent, ...], round_id: int) -> bool:
    for stored in events:
        event = stored.event
        if event.source != "shadow" or event.topic != "shadow.model_decision":
            continue
        value = event.payload.get("round_id")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("stored shadow decision round_id is invalid")
        if value == round_id:
            return True
    return False


def _blocked(
    artifact: PromotedModelArtifact,
    *,
    blockers: tuple[str, ...],
    round_id: int | None,
    source_tip_hash: str | None,
    snapshot_digest: str | None,
    source_event_count: int,
) -> ShadowInferenceResult:
    return ShadowInferenceResult(
        accepted=False,
        blockers=blockers,
        round_id=round_id,
        probability_bull=None,
        probability_bear=None,
        probability_tie=None,
        predicted_outcome=None,
        promoted_model_artifact_sha256=artifact.artifact_sha256,
        source_snapshot_tip_hash=source_tip_hash,
        source_snapshot_digest=snapshot_digest,
        source_snapshot_event_count=source_event_count,
        stored_decision=None,
    )


def _prepare_decision(
    artifact: PromotedModelArtifact,
    events: tuple[StoredEvent, ...],
    *,
    decision_at_ns: int,
    feature_policy: PortableFeaturePolicy,
    quality_policy: PortableQualityPolicy,
) -> _PreparedDecision | ShadowInferenceResult:
    if not events:
        return _blocked(
            artifact,
            blockers=("observed_store_empty",),
            round_id=None,
            source_tip_hash=None,
            snapshot_digest=None,
            source_event_count=0,
        )

    source_tip_hash = events[-1].event_hash
    digest = _snapshot_digest(events)
    future = [stored for stored in events if stored.event.observed_at_ns > decision_at_ns]
    if future:
        return _blocked(
            artifact,
            blockers=("future_observed_store_event",),
            round_id=_latest_round_id(events),
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )

    round_id = _latest_round_id(events)
    if round_id is None:
        return _blocked(
            artifact,
            blockers=("round_snapshot_missing",),
            round_id=None,
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )
    if _round_already_decided(events, round_id):
        return _blocked(
            artifact,
            blockers=("round_already_decided",),
            round_id=round_id,
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )

    snapshot = build_snapshot(events, cutoff_ns=decision_at_ns)
    try:
        features = build_portable_features(snapshot, policy=feature_policy)
    except ValueError as exc:
        return _blocked(
            artifact,
            blockers=(f"feature_unavailable:{exc}",),
            round_id=round_id,
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )

    try:
        quality = assess_portable_quality(snapshot, features, policy=quality_policy)
    except ValueError as exc:
        return _blocked(
            artifact,
            blockers=(f"quality_unavailable:{exc}",),
            round_id=round_id,
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )
    if not quality.ok:
        return _blocked(
            artifact,
            blockers=quality.blockers,
            round_id=round_id,
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )

    feature_values = features.as_dict()
    model = artifact.model
    if tuple(feature_values) != model.feature_names:
        return _blocked(
            artifact,
            blockers=("model_feature_schema_mismatch",),
            round_id=round_id,
            source_tip_hash=source_tip_hash,
            snapshot_digest=digest,
            source_event_count=len(events),
        )

    probability = model.predict(feature_values)
    predicted = probability.predicted.value
    return _PreparedDecision(
        round_id=round_id,
        features=features,
        quality=quality,
        probability_bull=probability.bull,
        probability_bear=probability.bear,
        probability_tie=probability.tie,
        predicted_outcome=predicted,
        source_tip_hash=source_tip_hash,
        snapshot_digest=digest,
        source_event_count=len(events),
        protocol_block_number=quality.latest_round_block_number,
    )


def infer_shadow_decision(
    artifact: PromotedModelArtifact,
    store: EventStore,
    *,
    clock_ns: ClockNs = time.time_ns,
    feature_policy: PortableFeaturePolicy = PortableFeaturePolicy(),
    quality_policy: PortableQualityPolicy = PortableQualityPolicy(),
) -> ShadowInferenceResult:
    """Create one model decision bound to the exact observed Event Store tip.

    A SQLite write lock freezes the source snapshot while features, quality and
    inference are computed. The decision event is inserted in the same
    transaction and its `prev_hash` is exactly the source tip used by the model.
    Blocked evaluations roll back and leave no decision record.
    """

    if store.mode != "observed":
        raise ValueError("shadow inference requires observed Event Store")
    artifact.validate()
    feature_policy.validate()
    quality_policy.validate()
    decision_at_ns = clock_ns()
    if decision_at_ns < 0:
        raise ValueError("clock returned negative decision timestamp")

    try:
        store._conn.execute("BEGIN IMMEDIATE")
        events = _read_all_locked(store)
        prepared = _prepare_decision(
            artifact,
            events,
            decision_at_ns=decision_at_ns,
            feature_policy=feature_policy,
            quality_policy=quality_policy,
        )
        if isinstance(prepared, ShadowInferenceResult):
            store._conn.rollback()
            return prepared

        feature_values = prepared.features.as_dict()
        decision = EventRecord(
            event_id=f"shadow:model_decision:{prepared.round_id}",
            source="shadow",
            topic="shadow.model_decision",
            event_time_ns=decision_at_ns,
            observed_at_ns=decision_at_ns,
            payload={
                "round_id": prepared.round_id,
                "decision_at_ns": decision_at_ns,
                "promoted_model_artifact_sha256": artifact.artifact_sha256,
                "source_dataset_artifact_sha256": str(
                    artifact.payload["source_dataset_artifact_sha256"]
                ),
                "source_snapshot_tip_hash": prepared.source_tip_hash,
                "source_snapshot_digest": prepared.snapshot_digest,
                "source_snapshot_event_count": prepared.source_event_count,
                "protocol_block_number": prepared.protocol_block_number,
                "features": feature_values,
                "probability": {
                    "bull": prepared.probability_bull,
                    "bear": prepared.probability_bear,
                    "tie": prepared.probability_tie,
                },
                "predicted_outcome": prepared.predicted_outcome,
                "feature_policy": asdict(feature_policy),
                "quality_policy": asdict(quality_policy),
                "quality": {
                    "ok": prepared.quality.ok,
                    "blockers": list(prepared.quality.blockers),
                    "round_observation_age_ns": prepared.quality.round_observation_age_ns,
                    "latest_protocol_block_number": prepared.quality.latest_protocol_block_number,
                    "latest_round_block_number": prepared.quality.latest_round_block_number,
                },
            },
        )
        decision.validate()
        if store.event_mode(decision) != "observed":
            raise AssertionError("shadow decision must be observed evidence")

        event_hash = store._hash(
            prepared.source_tip_hash,
            store._canonical_body(decision),
        )
        cursor = store._conn.execute(
            """
            INSERT INTO events(
                event_id, source, topic, event_time_ns, observed_at_ns,
                payload_json, prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.event_id,
                decision.source,
                decision.topic,
                decision.event_time_ns,
                decision.observed_at_ns,
                store._payload_json(decision),
                prepared.source_tip_hash,
                event_hash,
            ),
        )
        stored = StoredEvent(
            ingest_seq=int(cursor.lastrowid),
            event=decision,
            prev_hash=prepared.source_tip_hash,
            event_hash=event_hash,
        )
        store._conn.commit()
        return ShadowInferenceResult(
            accepted=True,
            blockers=(),
            round_id=prepared.round_id,
            probability_bull=prepared.probability_bull,
            probability_bear=prepared.probability_bear,
            probability_tie=prepared.probability_tie,
            predicted_outcome=prepared.predicted_outcome,
            promoted_model_artifact_sha256=artifact.artifact_sha256,
            source_snapshot_tip_hash=prepared.source_tip_hash,
            source_snapshot_digest=prepared.snapshot_digest,
            source_snapshot_event_count=prepared.source_event_count,
            stored_decision=stored,
        )
    except sqlite3.IntegrityError as exc:
        store._conn.rollback()
        raise ValueError("shadow decision insert conflicted with existing record") from exc
    except Exception:
        store._conn.rollback()
        raise
