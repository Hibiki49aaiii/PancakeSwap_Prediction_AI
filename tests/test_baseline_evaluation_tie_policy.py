from __future__ import annotations

from pancake_prediction_ai.baseline_evaluation import evaluate_baseline_walk_forward
from pancake_prediction_ai.dataset import TrainingExample
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.tie_prior import TiePriorPolicy, estimate_tie_prior
from pancake_prediction_ai.walk_forward_dataset import build_availability_safe_folds


def _binary_history(count: int = 30) -> list[TrainingExample]:
    rows: list[TrainingExample] = []
    for index in range(count):
        outcome = Outcome.BULL if index % 2 else Outcome.BEAR
        cutoff = (index + 1) * 100
        rows.append(
            TrainingExample(
                round_id=index,
                decision_cutoff_ns=cutoff,
                label_available_at_ns=cutoff + 10,
                features=(("x", 1.0 if outcome is Outcome.BULL else -1.0),),
                outcome=outcome,
            )
        )
    return rows


def test_fold_local_wilson_prior_matches_only_model_train_outcomes() -> None:
    examples = _binary_history()
    folds = build_availability_safe_folds(
        examples,
        min_train_size=15,
        test_size=3,
        purge_size=0,
        step_size=3,
    )
    policy = TiePriorPolicy()
    result = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=["x"],
        calibration_size=3,
        epochs=100,
        tie_prior_policy=policy,
        prior_strength=20.0,
    )
    assert result.folds
    first_fold = folds[0]
    first_model_train_indices = first_fold.train_indices[:-3]
    expected = estimate_tie_prior(
        (examples[index].outcome for index in first_model_train_indices),
        policy=policy,
    ).probability
    observed = result.folds[0]
    assert observed.training_prior_source == "fold_train_wilson"
    assert observed.training_prior == expected


def test_later_test_outcomes_cannot_change_earlier_fold_prior() -> None:
    examples = _binary_history()
    folds = build_availability_safe_folds(
        examples,
        min_train_size=15,
        test_size=3,
        purge_size=0,
        step_size=3,
    )
    policy = TiePriorPolicy()
    baseline = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=["x"],
        calibration_size=3,
        epochs=50,
        tie_prior_policy=policy,
        prior_strength=20.0,
    )
    first_prior = baseline.folds[0].training_prior

    for index in folds[0].test_indices:
        original = examples[index]
        examples[index] = TrainingExample(
            round_id=original.round_id,
            decision_cutoff_ns=original.decision_cutoff_ns,
            label_available_at_ns=original.label_available_at_ns,
            features=original.features,
            outcome=Outcome.TIE,
        )

    changed = evaluate_baseline_walk_forward(
        examples,
        folds,
        feature_names=["x"],
        calibration_size=3,
        epochs=50,
        tie_prior_policy=policy,
        prior_strength=20.0,
    )
    assert changed.folds[0].training_prior == first_prior
