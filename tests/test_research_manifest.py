from __future__ import annotations

import pytest

from pancake_prediction_ai.research_manifest import build_research_run_manifest


class FakeDataset:
    artifact_sha256 = "a" * 64
    payload = {
        "dataset_id": "dataset-a",
        "source_event_store": {
            "availability_mode": "reconstructed",
            "tip_hash": "f" * 64,
            "event_count": 123,
        },
        "assumptions": {"decision_lead_ns": 1},
        "feature_policy": {"long_window_ns": 10},
    }
    examples = (object(), object(), object())

    def validate(self) -> None:
        return None


class FakeEvaluation:
    artifact_sha256 = "b" * 64
    payload = {
        "source_dataset_artifact_sha256": "a" * 64,
        "source_dataset_id": "dataset-a",
        "result": {
            "aggregate_metrics": {
                "count": 40,
                "multiclass_brier_score": 0.4,
                "log_loss": 0.8,
                "top_label_accuracy": 0.6,
                "expected_calibration_error": 0.1,
                "tie_rate": 0.01,
            }
        },
    }

    def validate(self) -> None:
        return None


class FakeModel:
    artifact_sha256 = "c" * 64
    payload = {
        "source_dataset_artifact_sha256": "a" * 64,
        "source_dataset_id": "dataset-a",
        "training_cutoff_ns": 999,
        "eligible_round_count": 80,
    }

    def validate(self) -> None:
        return None


def test_manifest_binds_dataset_evaluation_and_model_lineage() -> None:
    manifest = build_research_run_manifest(
        FakeDataset(), FakeEvaluation(), FakeModel(), generated_at_ns=1000  # type: ignore[arg-type]
    )
    assert manifest.payload["evidence_origin"] == "reconstructed"
    assert manifest.payload["dataset_artifact_sha256"] == "a" * 64
    assert manifest.payload["evaluation_artifact_sha256"] == "b" * 64
    assert manifest.payload["promoted_model_artifact_sha256"] == "c" * 64
    assert manifest.payload["oos_metrics"]["count"] == 40
    assert len(manifest.artifact_sha256) == 64
    manifest.validate()


def test_manifest_rejects_evaluation_from_other_dataset() -> None:
    class WrongEvaluation(FakeEvaluation):
        payload = {**FakeEvaluation.payload, "source_dataset_artifact_sha256": "d" * 64}

    with pytest.raises(ValueError, match="evaluation artifact"):
        build_research_run_manifest(
            FakeDataset(), WrongEvaluation(), FakeModel(), generated_at_ns=1000  # type: ignore[arg-type]
        )


def test_manifest_rejects_model_from_other_dataset() -> None:
    class WrongModel(FakeModel):
        payload = {**FakeModel.payload, "source_dataset_artifact_sha256": "d" * 64}

    with pytest.raises(ValueError, match="promoted model"):
        build_research_run_manifest(
            FakeDataset(), FakeEvaluation(), WrongModel(), generated_at_ns=1000  # type: ignore[arg-type]
        )


def test_manifest_rejects_observed_dataset_origin() -> None:
    class ObservedDataset(FakeDataset):
        payload = {
            **FakeDataset.payload,
            "source_event_store": {
                **FakeDataset.payload["source_event_store"],
                "availability_mode": "observed",
            },
        }

    with pytest.raises(ValueError, match="reconstructed dataset evidence"):
        build_research_run_manifest(
            ObservedDataset(), FakeEvaluation(), FakeModel(), generated_at_ns=1000  # type: ignore[arg-type]
        )
