from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.dataset import TrainingExample
from pancake_prediction_ai.dataset_artifact import build_historical_dataset_artifact
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_dataset import HistoricalExampleBuildResult
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event
from pancake_prediction_ai.trained_model_artifact import (
    PromotedModelConfig,
    load_promoted_model_artifact,
    train_promoted_model_artifact,
)


def _source_event() -> EventRecord:
    raw = EventRecord(
        event_id="source",
        source="test",
        topic="history",
        event_time_ns=1,
        observed_at_ns=10,
        payload={"x": 1},
    )
    return reconstruct_event(raw, policy=ReconstructionPolicy("dataset-a", 0, 100))


def _dataset(store: EventStore, count: int = 36):
    pattern = (
        (Outcome.BEAR, -2.0),
        (Outcome.TIE, 0.0),
        (Outcome.BULL, 2.0),
    )
    examples = []
    for index in range(count):
        outcome, x = pattern[index % 3]
        cutoff = (index + 1) * 100
        examples.append(
            TrainingExample(
                round_id=index + 1,
                decision_cutoff_ns=cutoff,
                label_available_at_ns=cutoff + 10,
                features=(("x", x),),
                outcome=outcome,
            )
        )
    result = HistoricalExampleBuildResult(
        examples=tuple(examples),
        skipped=(),
        feature_names=("x",),
    )
    return build_historical_dataset_artifact(
        store,
        result,
        dataset_id="dataset-a",
        generated_at_ns=123,
        decision_lead_ns=20,
        assumed_binance_latency_ns=1,
        assumed_onchain_latency_ns=2,
    )


def _config() -> PromotedModelConfig:
    return PromotedModelConfig(
        calibration_size=3,
        learning_rate=0.1,
        epochs=500,
        prior_strength=20.0,
    )


def test_promoted_model_uses_only_labels_available_by_training_cutoff(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        dataset = _dataset(store)
        artifact = train_promoted_model_artifact(
            dataset,
            training_cutoff_ns=2_510,
            generated_at_ns=3_000,
            config=_config(),
        )

    # label availability is round*100 + 10, so rounds 1..25 are eligible.
    assert artifact.payload["eligible_round_count"] == 25
    assert artifact.payload["model_train_round_ids"] == list(range(1, 23))
    assert artifact.payload["calibration_round_ids"] == [23, 24, 25]
    assert 26 not in artifact.payload["model_train_round_ids"]
    assert artifact.payload["source_dataset_artifact_sha256"] == dataset.artifact_sha256
    assert artifact.model.predict({"x": 2.0}).bull > artifact.model.predict({"x": 2.0}).bear
    assert len(artifact.artifact_sha256) == 64


def test_promoted_model_artifact_is_deterministic_for_fixed_inputs(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        dataset = _dataset(store)
        first = train_promoted_model_artifact(
            dataset,
            training_cutoff_ns=3_610,
            generated_at_ns=4_000,
            config=_config(),
        )
        second = train_promoted_model_artifact(
            dataset,
            training_cutoff_ns=3_610,
            generated_at_ns=4_000,
            config=_config(),
        )
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.payload == second.payload


def test_write_load_roundtrip_preserves_exact_prediction(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        artifact = train_promoted_model_artifact(
            _dataset(store),
            training_cutoff_ns=3_610,
            generated_at_ns=4_000,
            config=_config(),
        )
        expected = artifact.model.predict({"x": 0.0})
        path = artifact.write(tmp_path / "model.json")

    loaded = load_promoted_model_artifact(path)
    assert loaded.artifact_sha256 == artifact.artifact_sha256
    assert loaded.model.predict({"x": 0.0}) == expected
    assert loaded.feature_names == ("x",)


def test_model_payload_tampering_is_detected(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        artifact = train_promoted_model_artifact(
            _dataset(store),
            training_cutoff_ns=3_610,
            generated_at_ns=4_000,
            config=_config(),
        )
        path = artifact.write(tmp_path / "model.json")

    document = json.loads(path.read_text())
    document["payload"]["model"]["weights"][0][0] += 1.0
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_promoted_model_artifact(path)


def test_insufficient_label_available_history_is_rejected(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        dataset = _dataset(store, count=9)
    with pytest.raises(ValueError, match="insufficient label-available"):
        train_promoted_model_artifact(
            dataset,
            training_cutoff_ns=310,
            generated_at_ns=1_000,
            config=_config(),
        )
