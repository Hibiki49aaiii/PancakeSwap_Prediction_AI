from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .dataset import TrainingExample
from .walk_forward import purged_walk_forward_splits


@dataclass(frozen=True, slots=True)
class AvailabilitySafeFold:
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    excluded_unavailable_label_indices: tuple[int, ...]
    test_information_cutoff_ns: int

    def validate(self, examples: Sequence[TrainingExample]) -> None:
        if not self.train_indices or not self.test_indices:
            raise ValueError("fold must contain train and test rows")
        if set(self.train_indices) & set(self.test_indices):
            raise ValueError("train/test overlap")
        for index in self.train_indices:
            if examples[index].label_available_at_ns > self.test_information_cutoff_ns:
                raise ValueError("training label was unavailable at test information cutoff")
        for index in self.test_indices:
            if examples[index].decision_cutoff_ns < self.test_information_cutoff_ns:
                raise ValueError("test row predates fold information cutoff")


def build_availability_safe_folds(
    examples: Sequence[TrainingExample],
    *,
    min_train_size: int,
    test_size: int,
    purge_size: int,
    step_size: int | None = None,
    max_train_size: int | None = None,
) -> tuple[AvailabilitySafeFold, ...]:
    """Create purged folds and remove train labels unknowable at test time.

    Input rows must be ordered by decision cutoff. Purging protects adjacent
    feature/label windows; explicit label-availability filtering independently
    protects against labels that settle after a later test decision.
    """

    if not examples:
        raise ValueError("examples are required")
    for previous, current in zip(examples, examples[1:]):
        if current.decision_cutoff_ns <= previous.decision_cutoff_ns:
            raise ValueError("examples must be strictly ordered by decision cutoff")

    raw_splits = purged_walk_forward_splits(
        len(examples),
        min_train_size=min_train_size,
        test_size=test_size,
        purge_size=purge_size,
        step_size=step_size,
        max_train_size=max_train_size,
    )
    folds: list[AvailabilitySafeFold] = []
    for split in raw_splits:
        test_indices = tuple(split.test_indices)
        information_cutoff = examples[test_indices[0]].decision_cutoff_ns
        safe_train: list[int] = []
        excluded: list[int] = []
        for index in split.train_indices:
            if examples[index].label_available_at_ns <= information_cutoff:
                safe_train.append(index)
            else:
                excluded.append(index)
        if len(safe_train) < min_train_size:
            continue
        fold = AvailabilitySafeFold(
            train_indices=tuple(safe_train),
            test_indices=test_indices,
            excluded_unavailable_label_indices=tuple(excluded),
            test_information_cutoff_ns=information_cutoff,
        )
        fold.validate(examples)
        folds.append(fold)
    return tuple(folds)
