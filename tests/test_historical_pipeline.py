from __future__ import annotations

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_pipeline import HistoricalPipeline, HistoricalPipelineConfig
from pancake_prediction_ai.historical_store import (
    reconstruction_dataset_id,
    reconstruction_prediction_interval_seconds,
)
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event


def _reconstruct(raw: EventRecord, dataset_id: str = "dataset-a") -> EventRecord:
    return reconstruct_event(
        raw,
        policy=ReconstructionPolicy(dataset_id, 0, 1_000),
    )


def _reconstructed(event_id: str, dataset_id: str) -> EventRecord:
    return _reconstruct(
        EventRecord(
            event_id=event_id,
            source="test",
            topic="history",
            event_time_ns=1,
            observed_at_ns=999,
            payload={"x": 1},
        ),
        dataset_id,
    )


def _lifecycle(kind: str, epoch: int, timestamp_s: int, price: int | None) -> EventRecord:
    return _reconstruct(
        EventRecord(
            event_id=f"{kind}:{epoch}",
            source="pancake_prediction",
            topic="prediction.round_lifecycle",
            event_time_ns=timestamp_s * 1_000_000_000,
            observed_at_ns=timestamp_s * 1_000_000_000,
            payload={"kind": kind, "epoch": epoch, "price": price},
        )
    )


def _round_snapshot(epoch: int, start_timestamp: int, lock_timestamp: int) -> EventRecord:
    return _reconstruct(
        EventRecord(
            event_id=f"snapshot:{epoch}",
            source="pancake_prediction",
            topic="prediction.round_snapshot",
            event_time_ns=(lock_timestamp - 10) * 1_000_000_000,
            observed_at_ns=(lock_timestamp - 10) * 1_000_000_000,
            payload={
                "epoch": epoch,
                "start_timestamp": start_timestamp,
                "lock_timestamp": lock_timestamp,
            },
        )
    )


def _config(dataset_id: str = "dataset-a") -> HistoricalPipelineConfig:
    return HistoricalPipelineConfig(
        dataset_id=dataset_id,
        decision_lead_ns=20_000_000_000,
        assumed_binance_latency_ns=500_000_000,
        assumed_onchain_latency_ns=1_000_000_000,
    )


def test_pipeline_constructor_persistently_binds_dataset_namespace(tmp_path) -> None:
    path = tmp_path / "history.sqlite"
    with EventStore(path, mode="reconstructed") as store:
        HistoricalPipeline(store, _config())
        assert reconstruction_dataset_id(store) == "dataset-a"
        store.append(_reconstructed("a", "dataset-a"))
        with pytest.raises(ValueError):
            store.append(_reconstructed("b", "dataset-b"))

    with EventStore(path, mode="reconstructed") as reopened:
        with pytest.raises(ValueError, match="bound to dataset dataset-a"):
            HistoricalPipeline(reopened, _config("dataset-b"))


def test_pipeline_recovers_scheduled_interval_from_round_snapshot_and_reopen(tmp_path) -> None:
    path = tmp_path / "history.sqlite"
    epoch = 77
    with EventStore(path, mode="reconstructed") as store:
        pipeline = HistoricalPipeline(store, _config())
        store.append_many(
            (
                _lifecycle("START", epoch, 1_000, None),
                _lifecycle("LOCK", epoch, 1_068, 100),  # operator is 8s late
                _lifecycle("END", epoch, 1_128, 101),
                _round_snapshot(epoch, 1_000, 1_060),
            )
        )
        timeline = pipeline.timelines().completed[0]
        assert timeline.lock_event.event_time_ns == 1_068_000_000_000
        assert timeline.lock_timestamp_ns == 1_060_000_000_000
        assert pipeline.config.prediction_interval_seconds == 60
        assert reconstruction_prediction_interval_seconds(store) == 60

    with EventStore(path, mode="reconstructed") as reopened:
        pipeline = HistoricalPipeline(reopened, _config())
        assert pipeline.config.prediction_interval_seconds == 60
        assert pipeline.timelines().completed[0].lock_timestamp_ns == 1_060_000_000_000


def test_pipeline_rejects_multiple_snapshot_interval_regimes(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        pipeline = HistoricalPipeline(store, _config())
        store.append_many(
            (
                _lifecycle("START", 1, 1_000, None),
                _lifecycle("LOCK", 1, 1_060, 100),
                _lifecycle("END", 1, 1_120, 101),
                _round_snapshot(1, 1_000, 1_060),
                _round_snapshot(2, 2_000, 2_300),
            )
        )
        with pytest.raises(ValueError, match="multiple Prediction intervals"):
            pipeline.timelines()


def test_pipeline_rejects_observed_store(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        with pytest.raises(ValueError, match="reconstructed Event Store"):
            HistoricalPipeline(store, _config())


def test_pipeline_backfill_methods_forward_locked_dataset_and_config(tmp_path, monkeypatch) -> None:
    seen = {}

    def fake_binance(client, store, **kwargs):
        seen["binance"] = kwargs
        return "binance-result"

    def fake_lifecycle(client, store, **kwargs):
        seen["lifecycle"] = kwargs
        return "lifecycle-result"

    monkeypatch.setattr(
        "pancake_prediction_ai.historical_pipeline.backfill_binance_aggregate_trades",
        fake_binance,
    )
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_pipeline.backfill_round_lifecycle_logs",
        fake_lifecycle,
    )

    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        pipeline = HistoricalPipeline(store, _config())
        assert pipeline.backfill_binance(
            object(),  # type: ignore[arg-type]
            symbol="BNBUSDT",
            start_time_ms=100,
            end_time_ms=200,
        ) == "binance-result"
        assert pipeline.backfill_lifecycle(
            object(),  # type: ignore[arg-type]
            from_block=10,
            to_block=20,
        ) == "lifecycle-result"

    assert seen["binance"]["dataset_id"] == "dataset-a"
    assert seen["binance"]["assumed_latency_ns"] == 500_000_000
    assert seen["lifecycle"]["dataset_id"] == "dataset-a"
    assert seen["lifecycle"]["assumed_latency_ns"] == 1_000_000_000


def test_pipeline_build_examples_requires_completed_lifecycle(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        pipeline = HistoricalPipeline(store, _config())
        with pytest.raises(ValueError, match="no completed round timelines"):
            pipeline.build_examples()
