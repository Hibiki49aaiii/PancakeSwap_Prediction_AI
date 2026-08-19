from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.dataset import TrainingExample
from pancake_prediction_ai.dataset_artifact import build_historical_dataset_artifact
from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.evaluation_artifact import (
    BaselineEvaluationConfig,
    evaluate_historical_dataset_artifact,
    load_baseline_evaluation_artifact,
)
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_dataset import HistoricalExampleBuildResult
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event


def _source_event() -> EventRecord:
    raw = EventRecord(
        event_id="raw:source",
        source="test",
        topic="history",
        event_time_ns=1,
        observed_at_ns=9,
        payload={"x": 1},
    )
    return reconstruct_event(
        raw,
        policy=ReconstructionPolicy("history-v1", 0, 100),
    )


def _examples(count: int = 36) -> HistoricalExampleBuildResult:
    pattern = (
        (Outcome.BEAR, -2.0),
        (Outcome.TIE, 0.0),
        (Outcome.BULL, 2.0),
    )
    rows: list[TrainingExample] = []
    for index in range(count):
        outcome, x = pattern[index % 3]
        cutoff = (index + 1) * 100
        rows.append(
            TrainingExample(
                round_id=index + 1,
                decision_cutoff_ns=cutoff,
                label_available_at_ns=cutoff + 10,
                features=(("x", x),),
                outcome=outcome,
            )
        )
    return HistoricalExampleBuildResult(
        examples=tuple(rows),
        skipped=(),
        feature_names=("x",),
    )


def _dataset(store: EventStore):
    return build_historical_dataset_artifact(
        store,
        _examples(),
        dataset_id="round-dataset-v1",
        generated_at_ns=123,
        decision_lead_ns=20,
        assumed_binance_latency_ns=1,
        assumed_onchain_latency_ns=2,
    )


def _config() -> BaselineEvaluationConfig:
    return BaselineEvaluationConfig(
        min_train_size=15,
        test_size=3,
        purge_size=1,
        calibration_size=3,
        step_size=3,
        learning_rate=0.1,
        epochs=300,
        prior_strength=20.0,
    )


def test_evaluation_artifact_binds_dataset_config_models_and_oos_predictions(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        dataset = _dataset(store)
        evaluation = evaluate_historical_dataset_artifact(
            dataset,
            generated_at_ns=456,
            config=_config(),
        )

    assert evaluation.payload["source_dataset_artifact_sha256"] == dataset.artifact_sha256
    assert evaluation.payload["source_store_tip_hash"] == dataset.payload["source_event_store"]["tip_hash"]
    result = evaluation.payload["result"]
    assert result["predictions"]
    assert result["folds"]
    assert result["aggregate_metrics"]["top_label_accuracy"] > 0.95
    assert all(fold["training_prior_source"] == "fold_train_wilson" for fold in result["folds"])
    assert len(evaluation.artifact_sha256) == 64


def test_same_dataset_config_and_generated_time_produce_same_digest(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        dataset = _dataset(store)
        first = evaluate_historical_dataset_artifact(dataset, generated_at_ns=456, config=_config())
        second = evaluate_historical_dataset_artifact(dataset, generated_at_ns=456, config=_config())
    assert first.artifact_sha256 == second.artifact_sha256
    assert first.payload == second.payload


def test_write_load_roundtrip_and_tamper_detection(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        evaluation = evaluate_historical_dataset_artifact(
            _dataset(store),
            generated_at_ns=456,
            config=_config(),
        )
        path = evaluation.write(tmp_path / "evaluation.json")

    loaded = load_baseline_evaluation_artifact(path)
    assert loaded.artifact_sha256 == evaluation.artifact_sha256

    document = json.loads(path.read_text())
    document["payload"]["result"]["aggregate_metrics"]["log_loss"] = 999.0
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_baseline_evaluation_artifact(path)


def test_configuration_with_no_walk_forward_fold_is_rejected(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append(_source_event())
        dataset = build_historical_dataset_artifact(
            store,
            _examples(6),
            dataset_id="small",
            generated_at_ns=1,
            decision_lead_ns=1,
            assumed_binance_latency_ns=0,
            assumed_onchain_latency_ns=0,
        )
    config = BaselineEvaluationConfig(
        min_train_size=5,
        test_size=3,
        purge_size=0,
        calibration_size=2,
        epochs=20,
    )
    with pytest.raises(ValueError, match="no availability-safe folds"):
        evaluate_historical_dataset_artifact(dataset, generated_at_ns=2, config=config)
