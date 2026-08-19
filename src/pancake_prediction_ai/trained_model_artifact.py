from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .baseline_model import (
    BaselineModel,
    FeatureScaler,
    LabeledFeatures,
    fit_softmax_baseline,
    fit_temperature,
)
from .dataset_artifact import HistoricalDatasetArtifact
from .metrics import OutcomeProbability
from .tie_prior import TiePriorPolicy, estimate_tie_prior


TRAINED_MODEL_ARTIFACT_SCHEMA = "promoted_softmax_model_v1"


@dataclass(frozen=True, slots=True)
class PromotedModelConfig:
    calibration_size: int
    learning_rate: float = 0.05
    epochs: int = 500
    l2: float = 1e-4
    prior_strength: float = 20.0
    tie_prior_policy: TiePriorPolicy = TiePriorPolicy()

    def validate(self) -> None:
        if self.calibration_size <= 0:
            raise ValueError("calibration_size must be positive")
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
            "calibration_size": self.calibration_size,
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
class PromotedModelArtifact:
    artifact_sha256: str
    payload: Mapping[str, Any]

    def validate(self) -> None:
        if len(self.artifact_sha256) != 64:
            raise ValueError("artifact_sha256 must be 64 hex characters")
        try:
            int(self.artifact_sha256, 16)
        except ValueError as exc:
            raise ValueError("artifact_sha256 must be hex") from exc
        if self.payload.get("schema") != TRAINED_MODEL_ARTIFACT_SCHEMA:
            raise ValueError("unsupported promoted model artifact schema")
        actual = hashlib.sha256(_canonical(self.payload)).hexdigest()
        if actual != self.artifact_sha256:
            raise ValueError("promoted model artifact SHA-256 mismatch")
        source_sha = self.payload.get("source_dataset_artifact_sha256")
        if not isinstance(source_sha, str) or len(source_sha) != 64:
            raise ValueError("source dataset artifact SHA-256 is invalid")
        model = _decode_model(self.payload)
        embedded_model_sha = self.payload.get("model_artifact_sha256")
        if model.artifact_sha256 != embedded_model_sha:
            raise ValueError("embedded model artifact SHA-256 mismatch")
        train_rounds = self.payload.get("model_train_round_ids")
        calibration_rounds = self.payload.get("calibration_round_ids")
        if not isinstance(train_rounds, list) or not isinstance(calibration_rounds, list):
            raise ValueError("training round lists are invalid")
        if not train_rounds or not calibration_rounds:
            raise ValueError("training/calibration round lists cannot be empty")
        if set(train_rounds) & set(calibration_rounds):
            raise ValueError("model train and calibration rounds overlap")

    @property
    def model(self) -> BaselineModel:
        self.validate()
        return _decode_model(self.payload)

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self.model.feature_names

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
        raise ValueError("promoted model payload must be canonical JSON serializable") from exc


def _decode_model(payload: Mapping[str, Any]) -> BaselineModel:
    raw = payload.get("model")
    if not isinstance(raw, dict):
        raise ValueError("promoted model payload is missing model")
    feature_names = raw.get("feature_names")
    means = raw.get("means")
    scales = raw.get("scales")
    weights = raw.get("weights")
    if not isinstance(feature_names, list) or not isinstance(means, list) or not isinstance(scales, list):
        raise ValueError("promoted model feature/scaler fields are invalid")
    if not isinstance(weights, list) or any(not isinstance(row, list) for row in weights):
        raise ValueError("promoted model weights are invalid")

    prior_raw = raw.get("training_prior")
    prior = None
    prior_strength = 0.0
    if prior_raw is not None:
        if not isinstance(prior_raw, dict):
            raise ValueError("promoted model training_prior is invalid")
        prior = OutcomeProbability(
            bull=float(prior_raw["bull"]),
            bear=float(prior_raw["bear"]),
            tie=float(prior_raw["tie"]),
        )
        prior.validate()
        prior_strength = float(prior_raw["strength"])

    model = BaselineModel(
        feature_names=tuple(str(value) for value in feature_names),
        scaler=FeatureScaler(
            means=tuple(float(value) for value in means),
            scales=tuple(float(value) for value in scales),
        ),
        weights=tuple(tuple(float(value) for value in row) for row in weights),
        temperature=float(raw["temperature"]),
        training_prior=prior,
        prior_strength=prior_strength,
    )
    model.validate()
    return model


def train_promoted_model_artifact(
    dataset: HistoricalDatasetArtifact,
    *,
    training_cutoff_ns: int,
    generated_at_ns: int,
    config: PromotedModelConfig,
) -> PromotedModelArtifact:
    """Train one shadow-candidate model using labels available by cutoff only."""

    dataset.validate()
    config.validate()
    if training_cutoff_ns <= 0:
        raise ValueError("training_cutoff_ns must be positive")
    if generated_at_ns < 0:
        raise ValueError("generated_at_ns must be non-negative")

    examples = dataset.examples
    for previous, current in zip(examples, examples[1:]):
        if current.decision_cutoff_ns <= previous.decision_cutoff_ns:
            raise ValueError("dataset examples must be strictly ordered by decision cutoff")

    eligible = [
        example
        for example in examples
        if example.label_available_at_ns <= training_cutoff_ns
    ]
    if len(eligible) <= config.calibration_size:
        raise ValueError("insufficient label-available examples for model/calibration split")

    model_train_examples = eligible[:-config.calibration_size]
    calibration_examples = eligible[-config.calibration_size:]
    prior_estimate = estimate_tie_prior(
        (example.outcome for example in model_train_examples),
        policy=config.tie_prior_policy,
    )
    model_train = [
        LabeledFeatures(example.feature_dict(), example.outcome)
        for example in model_train_examples
    ]
    calibration = [
        LabeledFeatures(example.feature_dict(), example.outcome)
        for example in calibration_examples
    ]
    model = fit_softmax_baseline(
        model_train,
        feature_names=dataset.feature_names,
        learning_rate=config.learning_rate,
        epochs=config.epochs,
        l2=config.l2,
        class_prior=prior_estimate.probability,
        prior_strength=config.prior_strength,
    )
    calibrated = fit_temperature(model, calibration)

    payload: dict[str, Any] = {
        "schema": TRAINED_MODEL_ARTIFACT_SCHEMA,
        "generated_at_ns": generated_at_ns,
        "training_cutoff_ns": training_cutoff_ns,
        "source_dataset_artifact_sha256": dataset.artifact_sha256,
        "source_dataset_id": dataset.payload.get("dataset_id"),
        "source_store_tip_hash": dataset.payload["source_event_store"]["tip_hash"],
        "configuration": config.payload(),
        "eligible_round_count": len(eligible),
        "model_train_round_ids": [example.round_id for example in model_train_examples],
        "calibration_round_ids": [example.round_id for example in calibration_examples],
        "tie_prior_estimate": {
            "sample_count": prior_estimate.sample_count,
            "bull_count": prior_estimate.bull_count,
            "bear_count": prior_estimate.bear_count,
            "tie_count": prior_estimate.tie_count,
            "empirical_tie_rate": prior_estimate.empirical_tie_rate,
            "wilson_tie_upper": prior_estimate.wilson_tie_upper,
            "probability": {
                "bull": prior_estimate.probability.bull,
                "bear": prior_estimate.probability.bear,
                "tie": prior_estimate.probability.tie,
            },
        },
        "model_artifact_sha256": calibrated.artifact_sha256,
        "model": calibrated.artifact_payload(),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    artifact = PromotedModelArtifact(digest, payload)
    artifact.validate()
    return artifact


def load_promoted_model_artifact(path: str | Path) -> PromotedModelArtifact:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("promoted model artifact could not be read") from exc
    if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
        raise ValueError("promoted model artifact document is invalid")
    artifact = PromotedModelArtifact(
        artifact_sha256=str(document.get("artifact_sha256", "")),
        payload=document["payload"],
    )
    artifact.validate()
    return artifact
