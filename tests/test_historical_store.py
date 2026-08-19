from __future__ import annotations

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_store import (
    bind_reconstruction_dataset,
    bind_reconstruction_prediction_interval_seconds,
    reconstruction_dataset_id,
    reconstruction_prediction_interval_seconds,
    verify_reconstruction_dataset_binding,
)
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event


def _event(event_id: str, dataset_id: str):
    raw = EventRecord(
        event_id=event_id,
        source="test",
        topic="history",
        event_time_ns=100,
        observed_at_ns=999,
        payload={"x": 1},
    )
    return reconstruct_event(
        raw,
        policy=ReconstructionPolicy(dataset_id, 1, 1_000),
    )


def test_first_binding_persists_dataset_id_and_trigger(tmp_path) -> None:
    path = tmp_path / "history.sqlite"
    with EventStore(path, mode="reconstructed") as store:
        assert bind_reconstruction_dataset(store, "dataset-a") == "dataset-a"
        store.append(_event("a", "dataset-a"))
        assert reconstruction_dataset_id(store) == "dataset-a"
        assert verify_reconstruction_dataset_binding(store)

    with EventStore(path, mode="reconstructed") as reopened:
        assert reconstruction_dataset_id(reopened) == "dataset-a"
        assert verify_reconstruction_dataset_binding(reopened)
        reopened.append(_event("b", "dataset-a"))
        assert len(reopened.read_all_ingest_order()) == 2


def test_prediction_interval_binding_persists_and_cannot_be_rebound(tmp_path) -> None:
    path = tmp_path / "history.sqlite"
    with EventStore(path, mode="reconstructed") as store:
        assert reconstruction_prediction_interval_seconds(store) is None
        assert bind_reconstruction_prediction_interval_seconds(store, 300) == 300
        assert reconstruction_prediction_interval_seconds(store) == 300
        with pytest.raises(ValueError, match="bound to Prediction interval 300"):
            bind_reconstruction_prediction_interval_seconds(store, 60)

    with EventStore(path, mode="reconstructed") as reopened:
        assert reconstruction_prediction_interval_seconds(reopened) == 300
        assert bind_reconstruction_prediction_interval_seconds(reopened, 300) == 300


def test_direct_low_level_append_of_other_dataset_is_blocked_by_sqlite_trigger(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        bind_reconstruction_dataset(store, "dataset-a")
        store.append(_event("a", "dataset-a"))
        with pytest.raises(ValueError, match="duplicate or conflicting"):
            store.append(_event("b", "dataset-b"))
        assert [item.event.event_id for item in store.read_all_ingest_order()] == [
            "reconstructed:dataset-a:a"
        ]
        assert store.verify_chain()


def test_reopen_cannot_rebind_to_different_dataset(tmp_path) -> None:
    path = tmp_path / "history.sqlite"
    with EventStore(path, mode="reconstructed") as store:
        bind_reconstruction_dataset(store, "dataset-a")
    with EventStore(path, mode="reconstructed") as reopened:
        with pytest.raises(ValueError, match="bound to dataset dataset-a"):
            bind_reconstruction_dataset(reopened, "dataset-b")


def test_binding_existing_single_dataset_backfill_is_supported(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_event("a", "dataset-a"))
        bind_reconstruction_dataset(store, "dataset-a")
        assert verify_reconstruction_dataset_binding(store)


def test_binding_detects_preexisting_mixed_dataset_contamination(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_event("a", "dataset-a"))
        store.append(_event("b", "dataset-b"))
        with pytest.raises(ValueError, match="contaminated"):
            bind_reconstruction_dataset(store, "dataset-a")


def test_observed_store_cannot_be_bound_as_reconstructed_dataset(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        with pytest.raises(ValueError, match="reconstructed Event Store"):
            bind_reconstruction_dataset(store, "dataset-a")
        with pytest.raises(ValueError, match="reconstructed Event Store"):
            bind_reconstruction_prediction_interval_seconds(store, 300)
