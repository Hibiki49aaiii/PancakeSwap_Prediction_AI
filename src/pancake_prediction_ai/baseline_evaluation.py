from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .baseline_model import LabeledFeatures, fit_softmax_baseline, fit_temperature
from .dataset import TrainingExample
from .metrics import OutcomeMetrics, OutcomeProbability, evaluate_outcome_probabilities
from .walk_forward_dataset import AvailabilitySafeFold


@dataclass(frozen=True, slots=True)
class OOSPrediction:
    round_id: int
    example_index: int
    probability: OutcomeProbability
    outcome: object
    model_artifact_sha256: str
    temperature: float


@dataclass(frozen=True, slots=True)
class FoldEvaluation:
    fold_index: int
    model_train_count: int
    calibration_count: int
    test_count: int
    model_artifact_sha256: str
    temperature: float
    metrics: OutcomeMetrics


@dataclass(frozen=True, slots=True)
class SkippedFold:
    fold_index: int
    reason: str


@dataclass(frozen=True, slots=True)
class BaselineWalkForwardResult:
    folds: tuple[FoldEvaluation, ...]
    skipped_folds: tuple[SkippedFold, ...]
    predictions: tuple[OOSPrediction, ...]
    aggregate_metrics: OutcomeMetrics | None


def _labeled(example: TrainingExample) -> LabeledFeatures:
    return LabeledFeatures(example.feature_dict(), example.outcome)


def evaluate_baseline_walk_forward(
    examples: Sequence[TrainingExample],
    folds: Sequence[AvailabilitySafeFold],
    *,
    feature_names: Sequence[str],
    calibration_size: int,
    learning_rate: float = 0.05,
    epochs: int = 500,
    l2: float = 1e-4,
) -> BaselineWalkForwardResult:
    """Train/calibrate/test a deterministic baseline without temporal reuse.

    Each fold's availability-safe training indices are split chronologically:
    the oldest rows fit model weights, the newest rows fit only temperature,
    and test rows are never used in either step. Overlapping test folds are
    rejected because counting one round more than once would bias OOS metrics.
    """

    if calibration_size <= 0:
        raise ValueError("calibration_size must be positive")
    if not examples:
        raise ValueError("examples are required")
    if not folds:
        raise ValueError("folds are required")

    predictions: list[OOSPrediction] = []
    fold_results: list[FoldEvaluation] = []
    skipped: list[SkippedFold] = []
    seen_test_indices: set[int] = set()

    for fold_index, fold in enumerate(folds):
        fold.validate(examples)
        overlap = seen_test_indices.intersection(fold.test_indices)
        if overlap:
            raise ValueError(f"test examples appear in multiple folds: {sorted(overlap)}")
        seen_test_indices.update(fold.test_indices)

        if len(fold.train_indices) <= calibration_size:
            skipped.append(SkippedFold(fold_index, "insufficient_train_after_calibration_split"))
            continue

        model_train_indices = fold.train_indices[:-calibration_size]
        calibration_indices = fold.train_indices[-calibration_size:]
        model_train = [_labeled(examples[index]) for index in model_train_indices]
        calibration = [_labeled(examples[index]) for index in calibration_indices]

        present = {row.outcome for row in model_train}
        if len(present) < 3:
            skipped.append(SkippedFold(fold_index, "model_train_missing_outcome_class"))
            continue

        model = fit_softmax_baseline(
            model_train,
            feature_names=feature_names,
            learning_rate=learning_rate,
            epochs=epochs,
            l2=l2,
        )
        calibrated = fit_temperature(model, calibration)

        fold_probabilities: list[OutcomeProbability] = []
        fold_outcomes = []
        fold_predictions: list[OOSPrediction] = []
        for index in fold.test_indices:
            example = examples[index]
            probability = calibrated.predict(example.feature_dict())
            prediction = OOSPrediction(
                round_id=example.round_id,
                example_index=index,
                probability=probability,
                outcome=example.outcome,
                model_artifact_sha256=calibrated.artifact_sha256,
                temperature=calibrated.temperature,
            )
            fold_predictions.append(prediction)
            fold_probabilities.append(probability)
            fold_outcomes.append(example.outcome)

        metrics = evaluate_outcome_probabilities(fold_probabilities, fold_outcomes)
        fold_results.append(
            FoldEvaluation(
                fold_index=fold_index,
                model_train_count=len(model_train_indices),
                calibration_count=len(calibration_indices),
                test_count=len(fold.test_indices),
                model_artifact_sha256=calibrated.artifact_sha256,
                temperature=calibrated.temperature,
                metrics=metrics,
            )
        )
        predictions.extend(fold_predictions)

    aggregate = None
    if predictions:
        aggregate = evaluate_outcome_probabilities(
            [prediction.probability for prediction in predictions],
            [prediction.outcome for prediction in predictions],
        )
    return BaselineWalkForwardResult(
        folds=tuple(fold_results),
        skipped_folds=tuple(skipped),
        predictions=tuple(predictions),
        aggregate_metrics=aggregate,
    )
