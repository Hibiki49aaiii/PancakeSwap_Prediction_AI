from __future__ import annotations

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.provenance import (
    AvailabilityMode,
    ReconstructionPolicy,
    availability_mode,
    reconstruct_event,
)


def _observed() -> EventRecord:
    return EventRecord(
        event_id="binance:spot:agg_trade:BNBUSDT:1",
        source="binance_spot",
        topic="market.agg_trade",
        event_time_ns=1_000,
        observed_at_ns=9_000,
        payload={"aggregate_trade_id": 1, "price": 600.0},
    )


def test_reconstructed_availability_is_explicit_assumption_not_fake_observation() -> None:
    raw = _observed()
    policy = ReconstructionPolicy(
        dataset_id="historical-v1",
        assumed_latency_ns=250,
        captured_at_ns=10_000,
        source_artifact_sha256="ab" * 32,
    )
    reconstructed = reconstruct_event(raw, policy=policy)
    assert availability_mode(raw) is AvailabilityMode.OBSERVED
    assert availability_mode(reconstructed) is AvailabilityMode.RECONSTRUCTED
    assert reconstructed.event_time_ns == 1_000
    assert reconstructed.observed_at_ns == 1_250
    assert reconstructed.observed_at_ns != raw.observed_at_ns
    metadata = reconstructed.payload["_availability_provenance"]
    assert metadata["captured_at_ns"] == 10_000
    assert metadata["assumed_latency_ns"] == 250
    assert metadata["source_artifact_sha256"] == "ab" * 32


def test_observed_store_rejects_reconstructed_events(tmp_path) -> None:
    reconstructed = reconstruct_event(
        _observed(),
        policy=ReconstructionPolicy("historical-v1", 100, 10_000),
    )
    with EventStore(tmp_path / "observed.sqlite") as store:
        with pytest.raises(ValueError, match="reconstructed event to observed"):
            store.append(reconstructed)
        assert store.read_all_ingest_order() == ()


def test_reconstructed_store_rejects_observed_events(tmp_path) -> None:
    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        with pytest.raises(ValueError, match="observed event to reconstructed"):
            store.append(_observed())
        assert store.read_all_ingest_order() == ()


def test_reconstructed_store_accepts_reconstructed_event_and_hashes_metadata(tmp_path) -> None:
    reconstructed = reconstruct_event(
        _observed(),
        policy=ReconstructionPolicy("historical-v1", 100, 10_000),
    )
    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        store.append(reconstructed)
        assert store.verify_chain()
        loaded = store.read_all_ingest_order()[0].event
        assert availability_mode(loaded) is AvailabilityMode.RECONSTRUCTED
        assert loaded.payload["_availability_provenance"]["dataset_id"] == "historical-v1"


def test_store_mode_is_persistent_and_cannot_be_reopened_as_other_evidence_class(tmp_path) -> None:
    path = tmp_path / "events.sqlite"
    with EventStore(path, mode="reconstructed"):
        pass
    with pytest.raises(ValueError, match="mode mismatch"):
        EventStore(path, mode="observed")
