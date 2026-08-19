from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .economics import Outcome
from .metrics import OutcomeProbability


_CLASS_ORDER = (Outcome.BULL, Outcome.BEAR, Outcome.TIE)
_CLASS_INDEX = {label: index for index, label in enumerate(_CLASS_ORDER)}


@dataclass(frozen=True, slots=True)
class FeatureScaler:
    means: tuple[float, ...]
    scales: tuple[float, ...]

    def transform(self, values: Sequence[float]) -> tuple[float, ...]:
        if len(values) != len(self.means):
            raise ValueError("feature vector length mismatch")
        return tuple(
            (float(value) - mean) / scale
            for value, mean, scale in zip(values, self.means, self.scales, strict=True)
        )


@dataclass(frozen=True, slots=True)
class BaselineModel:
    feature_names: tuple[str, ...]
    scaler: FeatureScaler
    weights: tuple[tuple[float, ...], ...]
    temperature: float = 1.0
    training_prior: OutcomeProbability | None = None
    prior_strength: float = 0.0

    def validate(self) -> None:
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature_names must be non-empty and unique")
        width = len(self.feature_names) + 1
        if len(self.weights) != 3 or any(len(row) != width for row in self.weights):
            raise ValueError("weights must be 3 x (features + bias)")
        if len(self.scaler.means) != len(self.feature_names) or len(self.scaler.scales) != len(self.feature_names):
            raise ValueError("scaler dimension mismatch")
        if any(scale <= 0 or not math.isfinite(scale) for scale in self.scaler.scales):
            raise ValueError("feature scales must be positive finite")
        if self.temperature <= 0 or not math.isfinite(self.temperature):
            raise ValueError("temperature must be positive finite")
        if self.prior_strength < 0 or not math.isfinite(self.prior_strength):
            raise ValueError("prior_strength must be non-negative finite")
        if self.training_prior is None and self.prior_strength != 0:
            raise ValueError("prior_strength requires training_prior")
        if self.training_prior is not None:
            self.training_prior.validate()
            if self.prior_strength <= 0:
                raise ValueError("training_prior requires positive prior_strength")
        for row in self.weights:
            if any(not math.isfinite(value) for value in row):
                raise ValueError("model weights must be finite")

    def _vector(self, features: Mapping[str, float]) -> tuple[float, ...]:
        try:
            raw = tuple(float(features[name]) for name in self.feature_names)
        except KeyError as exc:
            raise ValueError(f"missing model feature: {exc.args[0]}") from exc
        if any(not math.isfinite(value) for value in raw):
            raise ValueError("model features must be finite")
        return (1.0,) + self.scaler.transform(raw)

    def logits(self, features: Mapping[str, float]) -> tuple[float, float, float]:
        self.validate()
        vector = self._vector(features)
        result = tuple(
            sum(weight * value for weight, value in zip(row, vector, strict=True))
            for row in self.weights
        )
        return result[0], result[1], result[2]

    def predict(self, features: Mapping[str, float]) -> OutcomeProbability:
        logits = tuple(value / self.temperature for value in self.logits(features))
        probabilities = _softmax(logits)
        result = OutcomeProbability(probabilities[0], probabilities[1], probabilities[2])
        result.validate()
        return result

    def artifact_payload(self) -> dict[str, object]:
        self.validate()
        prior_payload = None
        if self.training_prior is not None:
            prior_payload = {
                "bull": self.training_prior.bull,
                "bear": self.training_prior.bear,
                "tie": self.training_prior.tie,
                "strength": self.prior_strength,
            }
        return {
            "model_type": "multinomial_softmax_baseline",
            "class_order": [label.value for label in _CLASS_ORDER],
            "feature_names": list(self.feature_names),
            "means": list(self.scaler.means),
            "scales": list(self.scaler.scales),
            "weights": [list(row) for row in self.weights],
            "temperature": self.temperature,
            "training_prior": prior_payload,
        }

    @property
    def artifact_sha256(self) -> str:
        canonical = json.dumps(
            self.artifact_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class LabeledFeatures:
    features: Mapping[str, float]
    outcome: Outcome


def _softmax(logits: Sequence[float]) -> tuple[float, ...]:
    maximum = max(logits)
    exps = tuple(math.exp(value - maximum) for value in logits)
    denominator = sum(exps)
    return tuple(value / denominator for value in exps)


def _fit_scaler(rows: Sequence[Sequence[float]]) -> FeatureScaler:
    if not rows:
        raise ValueError("at least one training row is required")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError("training feature rows must have fixed positive width")
    n = len(rows)
    means = tuple(sum(row[column] for row in rows) / n for column in range(width))
    variances = tuple(
        sum((row[column] - means[column]) ** 2 for row in rows) / n
        for column in range(width)
    )
    scales = tuple(math.sqrt(value) if value > 1e-24 else 1.0 for value in variances)
    return FeatureScaler(means=means, scales=scales)


def _extract_rows(
    examples: Sequence[LabeledFeatures],
    feature_names: tuple[str, ...],
) -> tuple[tuple[float, ...], ...]:
    rows: list[tuple[float, ...]] = []
    for example in examples:
        try:
            row = tuple(float(example.features[name]) for name in feature_names)
        except KeyError as exc:
            raise ValueError(f"missing training feature: {exc.args[0]}") from exc
        if any(not math.isfinite(value) for value in row):
            raise ValueError("training features must be finite")
        rows.append(row)
    return tuple(rows)


def fit_softmax_baseline(
    examples: Sequence[LabeledFeatures],
    *,
    feature_names: Sequence[str],
    learning_rate: float = 0.05,
    epochs: int = 500,
    l2: float = 1e-4,
    class_weights: Mapping[Outcome, float] | None = None,
    class_prior: OutcomeProbability | None = None,
    prior_strength: float = 0.0,
) -> BaselineModel:
    """Fit a deterministic 3-class softmax baseline.

    BULL and BEAR observations are mandatory. If TIE hasn't occurred in the
    training window, fitting remains forbidden unless the caller supplies an
    explicit 3-outcome prior with positive pseudo-count strength. This prevents
    an accidental binary collapse while still supporting legitimately rare
    house-win outcomes.
    """

    names = tuple(feature_names)
    if not names or len(set(names)) != len(names) or any(not name for name in names):
        raise ValueError("feature_names must be non-empty and unique")
    if not examples:
        raise ValueError("training examples are required")
    if learning_rate <= 0 or not math.isfinite(learning_rate):
        raise ValueError("learning_rate must be positive finite")
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if l2 < 0 or not math.isfinite(l2):
        raise ValueError("l2 must be non-negative finite")
    if prior_strength < 0 or not math.isfinite(prior_strength):
        raise ValueError("prior_strength must be non-negative finite")
    if class_prior is None and prior_strength != 0:
        raise ValueError("prior_strength requires class_prior")
    if class_prior is not None:
        class_prior.validate()
        if prior_strength <= 0:
            raise ValueError("class_prior requires positive prior_strength")

    present = {example.outcome for example in examples}
    missing_directional = [
        label.value for label in (Outcome.BULL, Outcome.BEAR) if label not in present
    ]
    if missing_directional:
        raise ValueError(
            f"BULL and BEAR observations are required for training; missing: {missing_directional}"
        )
    if Outcome.TIE not in present and class_prior is None:
        raise ValueError("TIE observation missing; explicit three-outcome class_prior is required")

    weights_by_class = {label: 1.0 for label in _CLASS_ORDER}
    if class_weights is not None:
        for label, value in class_weights.items():
            if label not in _CLASS_INDEX or value <= 0 or not math.isfinite(value):
                raise ValueError("class weights must be positive finite for known outcomes")
            weights_by_class[label] = float(value)

    rows = _extract_rows(examples, names)
    scaler = _fit_scaler(rows)
    x = tuple((1.0,) + scaler.transform(row) for row in rows)
    y = tuple(_CLASS_INDEX[example.outcome] for example in examples)
    sample_weights = tuple(weights_by_class[example.outcome] for example in examples)
    normalization = sum(sample_weights)
    width = len(names) + 1
    weights = [[0.0 for _ in range(width)] for _ in range(3)]
    prior_vector = None
    if class_prior is not None:
        prior_vector = (class_prior.bull, class_prior.bear, class_prior.tie)

    for _ in range(epochs):
        gradient = [[0.0 for _ in range(width)] for _ in range(3)]
        for vector, target, sample_weight in zip(x, y, sample_weights, strict=True):
            logits = [
                sum(weight * value for weight, value in zip(row, vector, strict=True))
                for row in weights
            ]
            probabilities = _softmax(logits)
            for class_index in range(3):
                error = (
                    probabilities[class_index] - (1.0 if class_index == target else 0.0)
                ) * sample_weight
                for column in range(width):
                    gradient[class_index][column] += error * vector[column]

        prior_probabilities = None
        if prior_vector is not None:
            prior_probabilities = _softmax([weights[index][0] for index in range(3)])

        for class_index in range(3):
            for column in range(width):
                grad = gradient[class_index][column] / normalization
                if column == 0 and prior_vector is not None and prior_probabilities is not None:
                    grad += (
                        prior_strength
                        / normalization
                        * (prior_probabilities[class_index] - prior_vector[class_index])
                    )
                if column != 0:
                    grad += l2 * weights[class_index][column]
                weights[class_index][column] -= learning_rate * grad

    model = BaselineModel(
        feature_names=names,
        scaler=scaler,
        weights=tuple(tuple(row) for row in weights),
        temperature=1.0,
        training_prior=class_prior,
        prior_strength=prior_strength,
    )
    model.validate()
    return model


def mean_log_loss(model: BaselineModel, examples: Sequence[LabeledFeatures]) -> float:
    if not examples:
        raise ValueError("evaluation examples are required")
    total = 0.0
    epsilon = 1e-15
    for example in examples:
        probability = model.predict(example.features).probability_of(example.outcome)
        total += -math.log(max(epsilon, probability))
    return total / len(examples)


def fit_temperature(
    model: BaselineModel,
    calibration_examples: Sequence[LabeledFeatures],
    *,
    lower: float = 0.25,
    upper: float = 4.0,
    iterations: int = 80,
) -> BaselineModel:
    """Fit one scalar temperature by deterministic golden-section NLL search."""

    model.validate()
    if not calibration_examples:
        raise ValueError("calibration examples are required")
    if lower <= 0 or upper <= lower:
        raise ValueError("temperature bounds are invalid")
    if iterations <= 0:
        raise ValueError("iterations must be positive")

    phi = (1.0 + math.sqrt(5.0)) / 2.0
    left = lower
    right = upper

    def objective(temperature: float) -> float:
        candidate = replace(model, temperature=temperature)
        return mean_log_loss(candidate, calibration_examples)

    c = right - (right - left) / phi
    d = left + (right - left) / phi
    fc = objective(c)
    fd = objective(d)
    for _ in range(iterations):
        if fc <= fd:
            right = d
            d = c
            fd = fc
            c = right - (right - left) / phi
            fc = objective(c)
        else:
            left = c
            c = d
            fc = fd
            d = left + (right - left) / phi
            fd = objective(d)

    temperature = (left + right) / 2.0
    calibrated = replace(model, temperature=temperature)
    calibrated.validate()
    return calibrated
