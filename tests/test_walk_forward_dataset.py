from __future__ import annotations

import pytest

from pancake_prediction_ai.dataset import TrainingExample
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.walk_forward_dataset import build_availability_safe_folds


def _examples() -> list[TrainingExample]:
    rows: list[TrainingExample] = []
    for index in range(10):
        cutoff = (index + 1) * 100
        label_available = cutoff + 50
        if index == 5:
            label_available = 900  # deliberately not knowable at the next test cutoff (700)
        rows.append(
            TrainingExample(
                round_id=index,
                decision_cutoff_ns=cutoff,
                label_available_at_ns=label_available,
                features=(("x", float(index)),),
                outcome=(Outcome.BULL, Outcome.BEAR, Outcome.TIE)[index % 3],
            )
        )
    return rows


def test_fold_excludes_training_label_that_was_not_yet_knowable() -> None:
    examples = _examples()
    folds = build_availability_safe_folds(
        examples,
        min_train_size=4,
        test_size=2,
        purge_size=0,
    )
    assert len(folds) >= 2
    second = folds[1]
    assert second.test_information_cutoff_ns == 700
    assert 5 in second.excluded_unavailable_label_indices
    assert 5 not in second.train_indices
    assert all(examples[index].label_available_at_ns <= 700 for index in second.train_indices)


def test_purge_and_label_availability_are_independent_guards() -> None:
    examples = _examples()
    folds = build_availability_safe_folds(
        examples,
        min_train_size=4,
        test_size=2,
        purge_size=1,
    )
    first = folds[0]
    assert max(first.train_indices) == 3
    assert min(first.test_indices) == 5
    assert 4 not in first.train_indices
    assert 4 not in first.test_indices


def test_examples_must_be_strictly_ordered_by_decision_time() -> None:
    examples = _examples()
    examples[2] = TrainingExample(
        round_id=2,
        decision_cutoff_ns=examples[1].decision_cutoff_ns,
        label_available_at_ns=999,
        features=(("x", 2.0),),
        outcome=Outcome.TIE,
    )
    with pytest.raises(ValueError, match="strictly ordered"):
        build_availability_safe_folds(
            examples,
            min_train_size=4,
            test_size=2,
            purge_size=0,
        )
