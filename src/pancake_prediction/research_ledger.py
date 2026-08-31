from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass

PPM = 1_000_000


@dataclass(frozen=True, slots=True)
class ResearchPredictionRecord:
    market: str
    epoch: int
    decision_timestamp_ms: int
    model_id: str
    feature_set_id: str
    raw_probability_ppm: int
    calibrated_probability_ppm: int
    expected_value_wei: int | None
    action: str
    feature_digest: str
    train_max_epoch: int
    metadata: Mapping[str, object] | None = None

    def canonical_payload(self) -> dict[str, object]:
        payload = asdict(self)
        if payload.get("metadata") is None:
            payload["metadata"] = {}
        return payload

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.canonical_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def feature_digest(features: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(features), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_research_prediction(
    record: ResearchPredictionRecord, *, purge_rounds: int = 2
) -> None:
    if record.epoch < 0 or record.decision_timestamp_ms < 0:
        raise ValueError("epoch and decision timestamp must be non-negative")
    for value in (record.raw_probability_ppm, record.calibrated_probability_ppm):
        if value < 0 or value > PPM:
            raise ValueError("probability must be in [0, 1_000_000]")
    if record.action not in ("bull", "bear", "skip"):
        raise ValueError("action must be bull, bear, or skip")
    if purge_rounds < 0:
        raise ValueError("purge_rounds must be non-negative")
    latest_allowed = record.epoch - purge_rounds - 1
    if record.train_max_epoch > latest_allowed:
        raise ValueError(
            "research prediction is not purged OOS: "
            f"epoch={record.epoch}, train_max_epoch={record.train_max_epoch}, "
            f"latest_allowed={latest_allowed}"
        )
    if len(record.feature_digest) != 64 or any(
        char not in "0123456789abcdef" for char in record.feature_digest.lower()
    ):
        raise ValueError("feature_digest must be a SHA-256 hex digest")
