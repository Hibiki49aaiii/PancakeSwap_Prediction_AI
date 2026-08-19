from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    train_start: int
    train_stop: int
    test_start: int
    test_stop: int

    @property
    def train_indices(self) -> range:
        return range(self.train_start, self.train_stop)

    @property
    def test_indices(self) -> range:
        return range(self.test_start, self.test_stop)


def purged_walk_forward_splits(
    n_samples: int,
    *,
    min_train_size: int,
    test_size: int,
    purge_size: int,
    step_size: int | None = None,
    max_train_size: int | None = None,
) -> tuple[WalkForwardSplit, ...]:
    """Create chronological expanding/rolling walk-forward splits.

    `purge_size` is the number of samples intentionally excluded between the
    end of training and beginning of testing. This protects round-adjacent
    labels/features from boundary leakage. A finite `max_train_size` turns the
    expanding window into a rolling window.
    """

    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    if min_train_size <= 0 or test_size <= 0:
        raise ValueError("train/test sizes must be positive")
    if purge_size < 0:
        raise ValueError("purge_size must be non-negative")
    if step_size is None:
        step_size = test_size
    if step_size <= 0:
        raise ValueError("step_size must be positive")
    if max_train_size is not None and max_train_size < min_train_size:
        raise ValueError("max_train_size must be >= min_train_size")

    splits: list[WalkForwardSplit] = []
    test_start = min_train_size + purge_size
    while test_start + test_size <= n_samples:
        train_stop = test_start - purge_size
        train_start = 0
        if max_train_size is not None:
            train_start = max(0, train_stop - max_train_size)
        if train_stop - train_start < min_train_size:
            test_start += step_size
            continue
        split = WalkForwardSplit(
            train_start=train_start,
            train_stop=train_stop,
            test_start=test_start,
            test_stop=test_start + test_size,
        )
        if split.train_stop + purge_size > split.test_start:
            raise AssertionError("purge invariant violated")
        splits.append(split)
        test_start += step_size
    return tuple(splits)
