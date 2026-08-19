from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.dataset import TrainingExample
from pancake_prediction_ai.dataset_artifact import (
    build_historical_dataset_artifact,
    load_historical_dataset_artifact,
)
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_dataset import ExampleSkip, HistoricalExampleBuildResult
from pancake_prediction_ai.portable_features import PortableFeaturePolicy
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event


def _historical_event(event_id: str = "raw:1") -> EventRecord:
    raw = EventRecord(
        event_id=event_id,
        source="binance_spot",
        topic="market.agg_trade",
        event_time_ns=1_000,
        observed_at_ns=9_000,
        payload={"aggregate_trade_id": 1, "price": 600.0},
    )
    return reconstruct_event(
        raw,
        policy=ReconstructionPolicy("history-v1", 100, 10_000),
    )


def _result() -> HistoricalExampleBuildResult:
    return HistoricalExampleBuildResult(
        examples=(
            TrainingExample(
                round_id=10,
                decision_cutoff_ns=10_000,
                label_available_at_ns=20_000,
                features=(("a", 1.0), ("b", 2.0)),
                outcome=Outcome.BULL,
            ),
            TrainingExample(
                round_id=11,
                decision_cutoff_ns=30_000,
                label_available_at_ns=40_000,
                features=(("a", 3.0), ("b", 4.0)),
                outcome=Outcome.TIE,
            ),
        ),
        skipped=(ExampleSkip(epoch=12, reason="feature_unavailable:test"),),
        feature_names=("a", "b"),
    )


def _artifact(store: EventStore):
    return build_historical_dataset_artifact(
        store,
        _result(),
        dataset_id="rounds-v1",
        generated_at_ns=123_456,
        decision_lead_ns=20_000_000_000,
        assumed_binance_latency_ns=500_000_000,
        assumed_onchain_latency_ns=1_000_000_000,
        feature_policy=PortableFeaturePolicy(
            long_window_ns=30_000_000_000,
            short_window_ns=5_000_000_000,
        ),
    )


def test_artifact_digest_is_reproducible_for_same_store_and_configuration(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_historical_event())
        first = _artifact(store)
        second = _artifact(store)
        assert first.artifact_sha256 == second.artifact_sha256
        assert first.payload == second.payload
        assert first.payload["source_event_store"]["tip_hash"] == store.read_all_ingest_order()[-1].event_hash


def test_write_load_roundtrip_preserves_examples_and_outcomes(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_historical_event())
        artifact = _artifact(store)
        path = artifact.write(tmp_path / "dataset.json")

    loaded = load_historical_dataset_artifact(path)
    assert loaded.artifact_sha256 == artifact.artifact_sha256
    assert loaded.feature_names == ("a", "b")
    assert [example.round_id for example in loaded.examples] == [10, 11]
    assert [example.outcome for example in loaded.examples] == [Outcome.BULL, Outcome.TIE]
    assert loaded.skipped == (ExampleSkip(epoch=12, reason="feature_unavailable:test"),)


def test_payload_tampering_is_detected(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_historical_event())
        path = _artifact(store).write(tmp_path / "dataset.json")

    document = json.loads(path.read_text())
    document["payload"]["examples"][0]["features"][0][1] = 999.0
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_historical_dataset_artifact(path)


def test_source_store_tamper_blocks_artifact_generation(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_historical_event())
        store._conn.execute(
            "UPDATE events SET payload_json = ? WHERE ingest_seq = 1",
            ('{"corrupt":true}',),
        )
        store._conn.commit()
        assert not store.verify_chain()
        with pytest.raises(ValueError, match="hash chain verification failed"):
            _artifact(store)


def test_observed_store_cannot_be_used_for_historical_dataset_artifact(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append(EventRecord("a", "test", "x", 1, 1, {"x": 1}))
        with pytest.raises(ValueError, match="reconstructed Event Store"):
            _artifact(store)


def test_duplicate_round_ids_are_rejected(tmp_path) -> None:
    bad = _result()
    duplicate = HistoricalExampleBuildResult(
        examples=(bad.examples[0], bad.examples[0]),
        skipped=(),
        feature_names=bad.feature_names,
    )
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_historical_event())
        with pytest.raises(ValueError, match="round IDs must be unique"):
            build_historical_dataset_artifact(
                store,
                duplicate,
                dataset_id="x",
                generated_at_ns=1,
                decision_lead_ns=1,
                assumed_binance_latency_ns=0,
                assumed_onchain_latency_ns=0,
            )


def test_future_leaking_label_is_rejected(tmp_path) -> None:
    bad = HistoricalExampleBuildResult(
        examples=(
            TrainingExample(
                round_id=1,
                decision_cutoff_ns=10,
                label_available_at_ns=10,
                features=(("a", 1.0),),
                outcome=Outcome.BEAR,
            ),
        ),
        skipped=(),
        feature_names=("a",),
    )
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_historical_event())
        with pytest.raises(ValueError, match="label must become available after"):
            build_historical_dataset_artifact(
                store,
                bad,
                dataset_id="x",
                generated_at_ns=1,
                decision_lead_ns=1,
                assumed_binance_latency_ns=0,
                assumed_onchain_latency_ns=0,
            )
