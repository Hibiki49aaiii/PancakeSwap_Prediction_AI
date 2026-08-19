from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .event_store import StoredEvent
from .replay import build_snapshot, latest_numeric


@dataclass(frozen=True, slots=True)
class NumericFeatureSpec:
    name: str
    source: str
    topic: str
    field: str

    def validate(self) -> None:
        if not all((self.name, self.source, self.topic, self.field)):
            raise ValueError("feature spec fields are required")


@dataclass(frozen=True, slots=True)
class RoundLabel:
    round_id: int
    outcome_bull: bool
    available_at_ns: int

    def validate(self) -> None:
        if self.round_id < 0 or self.available_at_ns < 0:
            raise ValueError("label round/time must be non-negative")


@dataclass(frozen=True, slots=True)
class TrainingExample:
    round_id: int
    decision_cutoff_ns: int
    label_available_at_ns: int
    features: tuple[tuple[str, float], ...]
    outcome_bull: bool

    def feature_dict(self) -> dict[str, float]:
        return dict(self.features)


def build_training_example(
    events: Iterable[StoredEvent],
    *,
    decision_cutoff_ns: int,
    label: RoundLabel,
    feature_specs: Iterable[NumericFeatureSpec],
) -> TrainingExample:
    label.validate()
    if decision_cutoff_ns < 0:
        raise ValueError("decision_cutoff_ns must be non-negative")
    if label.available_at_ns <= decision_cutoff_ns:
        raise ValueError("label must become available after decision cutoff")

    snapshot = build_snapshot(events, cutoff_ns=decision_cutoff_ns)
    values: list[tuple[str, float]] = []
    seen_names: set[str] = set()
    for spec in feature_specs:
        spec.validate()
        if spec.name in seen_names:
            raise ValueError(f"duplicate feature name: {spec.name}")
        seen_names.add(spec.name)
        value = latest_numeric(
            snapshot,
            source=spec.source,
            topic=spec.topic,
            field=spec.field,
        )
        if value is None:
            raise ValueError(f"missing feature at cutoff: {spec.name}")
        values.append((spec.name, value))

    return TrainingExample(
        round_id=label.round_id,
        decision_cutoff_ns=decision_cutoff_ns,
        label_available_at_ns=label.available_at_ns,
        features=tuple(values),
        outcome_bull=label.outcome_bull,
    )


def training_sort_key(example: TrainingExample) -> tuple[int, int]:
    """Sort by when the label was actually knowable, then round id."""

    return example.label_available_at_ns, example.round_id
