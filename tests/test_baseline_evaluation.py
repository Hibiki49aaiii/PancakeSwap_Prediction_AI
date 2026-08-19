from __future__ import annotations

import pytest

from pancake_prediction_ai.baseline_evaluation import evaluate_baseline_walk_forward
from pancake_prediction_ai.dataset import TrainingExample
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.metrics import OutcomeProbability
from pancake_prediction_ai.walk_forward_dataset import build_availability_safe_folds


def _examples(count: int = 36) -> list[TrainingExample]:
    pattern = (
        (Outcome.BEAR, -2.0),
        (Outcome.TIE, 0.0),
        (Outcome.BULL, 2.0),
    )
    rows: list[TrainingExample] = []
    for index in range(count):
        outcome, x = pattern[index % 3]
        cutoff = (index + 1) * 100
        rows.append(
            TrainingExample(
                round_id=index,
                decision_cutoff_ns=cutoff,
                label_available_at_ns=cutoff + 10,
                features=(("x", x),),
                outcome=outcome,
            )
        )
    return rows


def test_walk_forward_baseline_keeps_calibration_and_test_out_of_weight_training() -> None:
    examples = _examples()
    folds = build_availability_safe_folds(
        examples,
        min_train_size=15,
        test_size=3,
        purge_size=1,
        step_size=3,
    )
    result = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=["x"],
        calibration_size=3,
        learning_rate=0.1,
        epochs=600,
    )
    assert result.folds
    assert result.aggregate_metrics is not None
    assert result.aggregate_metrics.top_label_accuracy > 0.95
    assert len({prediction.example_index for prediction in result.predictions}) == len(result.predictions)
    for fold_result in result.folds:
        assert fold_result.calibration_count == 3
        assert fold_result.model_train_count >= 12
        assert fold_result.test_count == 3
        assert len(fold_result.model_artifact_sha256) == 64
        assert fold_result.temperature > 0


def test_fold_with_missing_tie_is_reported_without_explicit_prior() -> None:
    examples = _examples(18)
    for index in range(12):
        original = examples[index]
        outcome = Outcome.BULL if index % 2 else Outcome.BEAR
        examples[index] = TrainingExample(
            round_id=original.round_id,
            decision_cutoff_ns=original.decision_cutoff_ns,
            label_available_at_ns=original.label_available_at_ns,
            features=original.features,
            outcome=outcome,
        )
    folds = build_availability_safe_folds(
        examples,
        min_train_size=15,
        test_size=3,
        purge_size=0,
    )
    result = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=["x"],
        calibration_size=3,
        epochs=50,
    )
    assert result.skipped_folds
    assert result.skipped_folds[0].reason == "model_train_tie_missing_without_prior"


def test_fold_without_tie_can_run_with_explicit_three_outcome_prior() -> None:
    examples = _examples(24)
    for index, original in enumerate(examples):
        if original.outcome is Outcome.TIE:
            examples[index] = TrainingExample(
                round_id=original.round_id,
                decision_cutoff_ns=original.decision_cutoff_ns,
                label_available_at_ns=original.label_available_at_ns,
                features=original.features,
                outcome=Outcome.BULL if index % 2 else Outcome.BEAR,
            )
    folds = build_availability_safe_folds(
        examples,
        min_train_size=15,
        test_size=3,
        purge_size=0,
        step_size=3,
    )
    result = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=["x"],
        calibration_size=3,
        epochs=100,
        class_prior=OutcomeProbability(0.495, 0.495, 0.01),
        prior_strength=20.0,
    )
    assert result.folds
    assert not result.skipped_folds
    assert all(prediction.probability.tie > 0 for prediction in result.predictions)


def test_overlapping_test_folds_are_rejected_to_prevent_metric_double_counting() -> None:
    examples = _examples()
    folds = build_availability_safe_folds(
        examples,
        min_train_size=15,
        test_size=6,
        purge_size=0,
        step_size=3,
    )
    with pytest.raises(ValueError, match="multiple folds"):
        evaluate_baseline_walk_forward(
            examples,
            folds,
            feature_names=["x"],
            calibration_size=3,
            epochs=50,
        )
