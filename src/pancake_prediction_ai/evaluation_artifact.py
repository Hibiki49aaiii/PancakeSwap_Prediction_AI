from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .baseline_evaluation import BaselineWalkForwardResult, evaluate_baseline_walk_forward
from .dataset_artifact import HistoricalDatasetArtifact
from .metrics import CalibrationBin, OutcomeMetrics
from .tie_prior import TiePriorPolicy
from .walk_forward_dataset import build_availability_safe_folds


EVALUATION_ARTIFACT_SCHEMA = "baseline_oos_evaluation_v1"


@dataclass(frozen=True, slots=True)
class BaselineEvaluationConfig:
    min_train_size: int
    test_size: int
    purge_size: int
    calibration_size: int
    step_size: int | None = None
    max_train_size: int | None = None
    learning_rate: float = 0.05
    epochs: int = 500
    l2: float = 1e-4
    prior_strength: float = 20.0
    tie_prior_policy: TiePriorPolicy = TiePriorPolicy()

    def validate(self) -> None:
        if self.min_train_size <= 0 or self.test_size <= 0:
            raise ValueError("train/test sizes must be positive")
        if self.purge_size < 0:
            raise ValueError("purge_size must be non-negative")
        if self.calibration_size <= 0 or self.calibration_size >= self.min_train_size:
            raise ValueError("calibration_size must be positive and below min_train_size")
        if self.step_size is not None and self.step_size <= 0:
            raise ValueError("step_size must be positive")
        if self.max_train_size is not None and self.max_train_size < self.min_train_size:
            raise ValueError("max_train_size cannot be below min_train_size")
        if self.learning_rate <= 0 or not math.isfinite(self.learning_rate):
            raise ValueError("learning_rate must be positive finite")
        if self.epochs <= 0:
            raise ValueError("epochs must be positive")
        if self.l2 < 0 or not math.isfinite(self.l2):
            raise ValueError("l2 must be non-negative finite")
        if self.prior_strength <= 0 or not math.isfinite(self.prior_strength):
            raise ValueError("prior_strength must be positive finite")
        self.tie_prior_policy.validate()

    def payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "min_train_size": self.min_train_size,
            "test_size": self.test_size,
            "purge_size": self.purge_size,
            "calibration_size": self.calibration_size,
            "step_size": self.step_size,
            "max_train_size": self.max_train_size,
            "learning_rate": self.learning_rate,
            "epochs": self.epochs,
            "l2": self.l2,
            "prior_strength": self.prior_strength,
            "tie_prior_policy": {
                "z_score": self.tie_prior_policy.z_score,
                "directional_alpha": self.tie_prior_policy.directional_alpha,
                "minimum_tie_probability": self.tie_prior_policy.minimum_tie_probability,
            },
        }


@dataclass(frozen=True, slots=True)
class BaselineEvaluationArtifact:
    artifact_sha256: str
    payload: Mapping[str, Any]

    def validate(self) -> None:
        if len(self.artifact_sha256) != 64:
            raise ValueError("artifact_sha256 must be 64 hex characters")
        try:
            int(self.artifact_sha256, 16)
        except ValueError as exc:
            raise ValueError("artifact_sha256 must be hex") from exc
        if self.payload.get("schema") != EVALUATION_ARTIFACT_SCHEMA:
            raise ValueError("unsupported evaluation artifact schema")
        expected = hashlib.sha256(_canonical(self.payload)).hexdigest()
        if expected != self.artifact_sha256:
            raise ValueError("evaluation artifact SHA-256 mismatch")
        dataset_sha = self.payload.get("source_dataset_artifact_sha256")
        if not isinstance(dataset_sha, str) or len(dataset_sha) != 64:
            raise ValueError("source dataset artifact SHA-256 is invalid")
        result = self.payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("evaluation artifact result is invalid")
        predictions = result.get("predictions")
        if not isinstance(predictions, list):
            raise ValueError("evaluation artifact predictions are invalid")
        indices = [int(row["example_index"]) for row in predictions]
        if len(set(indices)) != len(indices):
            raise ValueError("evaluation artifact contains duplicate OOS example indices")

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
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("evaluation payload must be canonical JSON serializable") from exc


def _bin_payload(bin_: CalibrationBin) -> dict[str, Any]:
    return {
        "lower": bin_.lower,
        "upper": bin_.upper,
        "count": bin_.count,
        "mean_probability": bin_.mean_probability,
        "empirical_rate": bin_.empirical_rate,
        "absolute_gap": bin_.absolute_gap,
    }


def _metrics_payload(metrics: OutcomeMetrics) -> dict[str, Any]:
    return {
        "count": metrics.count,
        "multiclass_brier_score": metrics.multiclass_brier_score,
        "log_loss": metrics.log_loss,
        "top_label_accuracy": metrics.top_label_accuracy,
        "expected_calibration_error": metrics.expected_calibration_error,
        "tie_rate": metrics.tie_rate,
        "calibration_bins": [_bin_payload(bin_) for bin_ in metrics.calibration_bins],
    }


def _result_payload(result: BaselineWalkForwardResult) -> dict[str, Any]:
    aggregate = None
    if result.aggregate_metrics is not None:
        aggregate = _metrics_payload(result.aggregate_metrics)
    return {
        "folds": [
            {
                "fold_index": fold.fold_index,
                "model_train_count": fold.model_train_count,
                "calibration_count": fold.calibration_count,
                "test_count": fold.test_count,
                "model_artifact_sha256": fold.model_artifact_sha256,
                "temperature": fold.temperature,
                "training_prior_source": fold.training_prior_source,
                "training_prior": None
                if fold.training_prior is None
                else {
                    "bull": fold.training_prior.bull,
                    "bear": fold.training_prior.bear,
                    "tie": fold.training_prior.tie,
                },
                "metrics": _metrics_payload(fold.metrics),
            }
            for fold in result.folds
        ],
        "skipped_folds": [
            {"fold_index": skipped.fold_index, "reason": skipped.reason}
            for skipped in result.skipped_folds
        ],
        "predictions": [
            {
                "round_id": prediction.round_id,
                "example_index": prediction.example_index,
                "outcome": prediction.outcome.value,
                "probability": {
                    "bull": prediction.probability.bull,
                    "bear": prediction.probability.bear,
                    "tie": prediction.probability.tie,
                },
                "model_artifact_sha256": prediction.model_artifact_sha256,
                "temperature": prediction.temperature,
            }
            for prediction in result.predictions
        ],
        "aggregate_metrics": aggregate,
    }


def evaluate_historical_dataset_artifact(
    dataset: HistoricalDatasetArtifact,
    *,
    generated_at_ns: int,
    config: BaselineEvaluationConfig,
) -> BaselineEvaluationArtifact:
    dataset.validate()
    config.validate()
    if generated_at_ns < 0:
        raise ValueError("generated_at_ns must be non-negative")
    examples = dataset.examples
    folds = build_availability_safe_folds(
        examples,
        min_train_size=config.min_train_size,
        test_size=config.test_size,
        purge_size=config.purge_size,
        step_size=config.step_size,
        max_train_size=config.max_train_size,
    )
    if not folds:
        raise ValueError("evaluation configuration produced no availability-safe folds")
    result = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=dataset.feature_names,
        calibration_size=config.calibration_size,
        learning_rate=config.learning_rate,
        epochs=config.epochs,
        l2=config.l2,
        tie_prior_policy=config.tie_prior_policy,
        prior_strength=config.prior_strength,
    )
    if result.aggregate_metrics is None or not result.predictions:
        raise ValueError("evaluation produced no OOS predictions")

    payload: dict[str, Any] = {
        "schema": EVALUATION_ARTIFACT_SCHEMA,
        "generated_at_ns": generated_at_ns,
        "source_dataset_artifact_sha256": dataset.artifact_sha256,
        "source_dataset_id": dataset.payload.get("dataset_id"),
        "source_store_tip_hash": dataset.payload["source_event_store"]["tip_hash"],
        "configuration": config.payload(),
        "result": _result_payload(result),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    artifact = BaselineEvaluationArtifact(digest, payload)
    artifact.validate()
    return artifact


def load_baseline_evaluation_artifact(path: str | Path) -> BaselineEvaluationArtifact:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("baseline evaluation artifact could not be read") from exc
    if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
        raise ValueError("baseline evaluation artifact document is invalid")
    artifact = BaselineEvaluationArtifact(
        artifact_sha256=str(document.get("artifact_sha256", "")),
        payload=document["payload"],
    )
    artifact.validate()
    return artifact
