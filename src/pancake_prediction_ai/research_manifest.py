from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .dataset_artifact import HistoricalDatasetArtifact
from .evaluation_artifact import BaselineEvaluationArtifact
from .trained_model_artifact import PromotedModelArtifact


RESEARCH_MANIFEST_SCHEMA = "research_run_manifest_v1"


@dataclass(frozen=True, slots=True)
class ResearchRunManifest:
    artifact_sha256: str
    payload: Mapping[str, Any]

    def validate(self) -> None:
        if len(self.artifact_sha256) != 64:
            raise ValueError("manifest artifact_sha256 must be 64 hex characters")
        try:
            int(self.artifact_sha256, 16)
        except ValueError as exc:
            raise ValueError("manifest artifact_sha256 must be hex") from exc
        if self.payload.get("schema") != RESEARCH_MANIFEST_SCHEMA:
            raise ValueError("unsupported research manifest schema")
        if hashlib.sha256(_canonical(self.payload)).hexdigest() != self.artifact_sha256:
            raise ValueError("research manifest SHA-256 mismatch")
        dataset_sha = self.payload.get("dataset_artifact_sha256")
        evaluation_sha = self.payload.get("evaluation_artifact_sha256")
        model_sha = self.payload.get("promoted_model_artifact_sha256")
        for label, value in (
            ("dataset", dataset_sha),
            ("evaluation", evaluation_sha),
            ("model", model_sha),
        ):
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError(f"{label} artifact SHA-256 is invalid")
        if self.payload.get("evidence_origin") != "reconstructed":
            raise ValueError("research run manifest must remain reconstructed evidence")

    def write(self, path: str | Path) -> Path:
        self.validate()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            {"artifact_sha256": self.artifact_sha256, "payload": self.payload},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def build_research_run_manifest(
    dataset: HistoricalDatasetArtifact,
    evaluation: BaselineEvaluationArtifact,
    model: PromotedModelArtifact,
    *,
    generated_at_ns: int,
) -> ResearchRunManifest:
    dataset.validate()
    evaluation.validate()
    model.validate()
    if generated_at_ns < 0:
        raise ValueError("generated_at_ns must be non-negative")

    evaluation_dataset_sha = evaluation.payload.get("source_dataset_artifact_sha256")
    model_dataset_sha = model.payload.get("source_dataset_artifact_sha256")
    if evaluation_dataset_sha != dataset.artifact_sha256:
        raise ValueError("evaluation artifact does not belong to supplied dataset")
    if model_dataset_sha != dataset.artifact_sha256:
        raise ValueError("promoted model does not belong to supplied dataset")

    dataset_id = dataset.payload.get("dataset_id")
    if evaluation.payload.get("source_dataset_id") != dataset_id:
        raise ValueError("evaluation dataset_id does not match supplied dataset")
    if model.payload.get("source_dataset_id") != dataset_id:
        raise ValueError("model dataset_id does not match supplied dataset")

    store = dataset.payload.get("source_event_store")
    if not isinstance(store, dict):
        raise ValueError("dataset source_event_store metadata is invalid")
    if store.get("availability_mode") != "reconstructed":
        raise ValueError("research manifest requires reconstructed dataset evidence")

    result = evaluation.payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("aggregate_metrics"), dict):
        raise ValueError("evaluation aggregate metrics are missing")
    metrics = result["aggregate_metrics"]

    payload: dict[str, Any] = {
        "schema": RESEARCH_MANIFEST_SCHEMA,
        "generated_at_ns": generated_at_ns,
        "evidence_origin": "reconstructed",
        "dataset_id": dataset_id,
        "dataset_artifact_sha256": dataset.artifact_sha256,
        "evaluation_artifact_sha256": evaluation.artifact_sha256,
        "promoted_model_artifact_sha256": model.artifact_sha256,
        "source_event_store_tip_hash": store.get("tip_hash"),
        "source_event_store_event_count": store.get("event_count"),
        "dataset_example_count": len(dataset.examples),
        "model_training_cutoff_ns": model.payload.get("training_cutoff_ns"),
        "model_eligible_round_count": model.payload.get("eligible_round_count"),
        "oos_metrics": {
            "count": metrics.get("count"),
            "multiclass_brier_score": metrics.get("multiclass_brier_score"),
            "log_loss": metrics.get("log_loss"),
            "top_label_accuracy": metrics.get("top_label_accuracy"),
            "expected_calibration_error": metrics.get("expected_calibration_error"),
            "tie_rate": metrics.get("tie_rate"),
        },
        "assumptions": dataset.payload.get("assumptions"),
        "feature_policy": dataset.payload.get("feature_policy"),
        "notes": [
            "This manifest binds reconstructed historical research artifacts only.",
            "It is not observed shadow evidence, local-fork evidence, or profitability proof.",
        ],
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    manifest = ResearchRunManifest(digest, payload)
    manifest.validate()
    return manifest
