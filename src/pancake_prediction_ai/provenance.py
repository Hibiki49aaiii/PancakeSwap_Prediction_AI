from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .event_store import EventRecord


_RESERVED_KEY = "_availability_provenance"


class AvailabilityMode(StrEnum):
    OBSERVED = "observed"
    RECONSTRUCTED = "reconstructed"


@dataclass(frozen=True, slots=True)
class ReconstructionPolicy:
    dataset_id: str
    assumed_latency_ns: int
    captured_at_ns: int
    source_artifact_sha256: str | None = None

    def validate(self) -> None:
        if not self.dataset_id:
            raise ValueError("dataset_id is required")
        if self.assumed_latency_ns < 0:
            raise ValueError("assumed_latency_ns must be non-negative")
        if self.captured_at_ns < 0:
            raise ValueError("captured_at_ns must be non-negative")
        if self.source_artifact_sha256 is not None:
            value = self.source_artifact_sha256
            if len(value) != 64:
                raise ValueError("source_artifact_sha256 must be 64 hex characters")
            try:
                int(value, 16)
            except ValueError as exc:
                raise ValueError("source_artifact_sha256 must be hex") from exc


def availability_mode(event: EventRecord) -> AvailabilityMode:
    metadata = event.payload.get(_RESERVED_KEY)
    if metadata is None:
        return AvailabilityMode.OBSERVED
    if not isinstance(metadata, dict):
        raise ValueError("availability provenance metadata must be an object")
    mode = metadata.get("mode")
    try:
        return AvailabilityMode(str(mode))
    except ValueError as exc:
        raise ValueError(f"unknown availability provenance mode: {mode}") from exc


def reconstruct_event(
    event: EventRecord,
    *,
    policy: ReconstructionPolicy,
) -> EventRecord:
    """Create a historical replay record with explicit assumed availability.

    The new `observed_at_ns` is an *assumption* (`event_time + latency`), never a
    claim that this process actually observed the event in the past. The actual
    backfill capture time and optional source-artifact digest stay in the hashed
    payload provenance metadata.
    """

    event.validate()
    policy.validate()
    if availability_mode(event) is not AvailabilityMode.OBSERVED:
        raise ValueError("only observed/raw events may be reconstructed")

    payload = dict(event.payload)
    payload[_RESERVED_KEY] = {
        "mode": AvailabilityMode.RECONSTRUCTED.value,
        "dataset_id": policy.dataset_id,
        "assumed_latency_ns": policy.assumed_latency_ns,
        "captured_at_ns": policy.captured_at_ns,
        "source_artifact_sha256": policy.source_artifact_sha256,
    }
    return EventRecord(
        event_id=f"reconstructed:{policy.dataset_id}:{event.event_id}",
        source=event.source,
        topic=event.topic,
        event_time_ns=event.event_time_ns,
        observed_at_ns=event.event_time_ns + policy.assumed_latency_ns,
        payload=payload,
    )
