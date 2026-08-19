from __future__ import annotations

import pytest

from pancake_prediction_ai.dataset import NumericFeatureSpec, RoundLabel, build_training_example
from pancake_prediction_ai.event_store import EventRecord, EventStore


def test_training_example_uses_only_pre_cutoff_observations(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(EventRecord("p1", "binance", "spot", 90, 100, {"price": 600.0}))
        store.append(EventRecord("p2", "binance", "spot", 80, 250, {"price": 999.0}))
        events = store.read_all_ingest_order()

    example = build_training_example(
        events,
        decision_cutoff_ns=200,
        label=RoundLabel(round_id=7, outcome_bull=True, available_at_ns=400),
        feature_specs=[NumericFeatureSpec("spot_price", "binance", "spot", "price")],
    )
    assert example.feature_dict()["spot_price"] == 600.0
    assert example.label_available_at_ns == 400


def test_label_cannot_be_available_at_or_before_decision_cutoff(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(EventRecord("p1", "binance", "spot", 90, 100, {"price": 600.0}))
        events = store.read_all_ingest_order()

    with pytest.raises(ValueError, match="label must become available after"):
        build_training_example(
            events,
            decision_cutoff_ns=200,
            label=RoundLabel(round_id=7, outcome_bull=True, available_at_ns=200),
            feature_specs=[NumericFeatureSpec("spot_price", "binance", "spot", "price")],
        )


def test_missing_feature_at_cutoff_is_hard_failure(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        events = store.read_all_ingest_order()

    with pytest.raises(ValueError, match="missing feature"):
        build_training_example(
            events,
            decision_cutoff_ns=200,
            label=RoundLabel(round_id=7, outcome_bull=False, available_at_ns=400),
            feature_specs=[NumericFeatureSpec("spot_price", "binance", "spot", "price")],
        )
