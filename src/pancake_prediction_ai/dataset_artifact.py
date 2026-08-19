from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .dataset import TrainingExample
from .economics import Outcome
from .event_store import EventStore
from .historical_dataset import ExampleSkip, HistoricalExampleBuildResult
from .portable_features import PortableFeaturePolicy


DATASET_ARTIFACT_SCHEMA = "historical_round_dataset_v1"


@dataclass(frozen=True, slots=True)
class HistoricalDatasetArtifact:
    artifact_sha256: str
    payload: Mapping[str, Any]

    def validate(self) -> None:
        if len(self.artifact_sha256) != 64:
            raise ValueError("artifact_sha256 must be 64 hex characters")
        try:
            int(self.artifact_sha256, 16)
        except ValueError as exc:
            raise ValueError("artifact_sha256 must be hex") from exc
        canonical = _canonical_payload(self.payload)
        actual = hashlib.sha256(canonical).hexdigest()
        if actual != self.artifact_sha256:
            raise ValueError("dataset artifact SHA-256 mismatch")
        _validate_payload(self.payload)

    @property
    def feature_names(self) -> tuple[str, ...]:
        self.validate()
        return tuple(str(value) for value in self.payload["feature_names"])

    @property
    def examples(self) -> tuple[TrainingExample, ...]:
        self.validate()
        return _decode_examples(self.payload["examples"])

    @property
    def skipped(self) -> tuple[ExampleSkip, ...]:
        self.validate()
        rows = self.payload["skipped"]
        assert isinstance(rows, list)
        return tuple(
            ExampleSkip(epoch=int(row["epoch"]), reason=str(row["reason"]))
            for row in rows
        )

    def write(self, path: str | Path) -> Path:
        self.validate()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "artifact_sha256": self.artifact_sha256,
            "payload": self.payload,
        }
        encoded = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination


def _canonical_payload(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("dataset payload must be canonical JSON serializable") from exc


def _store_tip(store: EventStore) -> str:
    events = store.read_all_ingest_order()
    if not events:
        raise ValueError("historical dataset artifact requires non-empty source Event Store")
    return events[-1].event_hash


def _feature_policy_payload(policy: PortableFeaturePolicy) -> dict[str, int]:
    policy.validate()
    return {
        "long_window_ns": policy.long_window_ns,
        "short_window_ns": policy.short_window_ns,
        "max_source_clock_skew_ns": policy.max_source_clock_skew_ns,
    }


def _example_payload(example: TrainingExample) -> dict[str, Any]:
    if example.decision_cutoff_ns < 0:
        raise ValueError("decision cutoff must be non-negative")
    if example.label_available_at_ns <= example.decision_cutoff_ns:
        raise ValueError("label must become available after decision cutoff")
    if example.round_id < 0:
        raise ValueError("round_id must be non-negative")
    names = [name for name, _ in example.features]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate feature in round {example.round_id}")
    return {
        "round_id": example.round_id,
        "decision_cutoff_ns": example.decision_cutoff_ns,
        "label_available_at_ns": example.label_available_at_ns,
        "features": [[name, float(value)] for name, value in example.features],
        "outcome": example.outcome.value,
    }


def build_historical_dataset_artifact(
    store: EventStore,
    result: HistoricalExampleBuildResult,
    *,
    dataset_id: str,
    generated_at_ns: int,
    decision_lead_ns: int,
    assumed_binance_latency_ns: int,
    assumed_onchain_latency_ns: int,
    feature_policy: PortableFeaturePolicy = PortableFeaturePolicy(),
    prediction_interval_seconds: int | None = None,
    subgraph_availability_latency_ns: int | None = None,
) -> HistoricalDatasetArtifact:
    """Freeze historical examples and source-store identity into one digest.

    Source availability assumptions that affect the replay cutoff are explicit
    artifact inputs. Subgraph/indexer latency is optional for legacy/RPC-only
    datasets but mandatory in practice for the Prediction subgraph acquisition
    path and is digest-bound when supplied.
    """

    if store.mode != "reconstructed":
        raise ValueError("historical dataset artifact requires reconstructed Event Store")
    if not store.verify_chain():
        raise ValueError("source Event Store hash chain verification failed")
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if generated_at_ns < 0:
        raise ValueError("generated_at_ns must be non-negative")
    if decision_lead_ns <= 0:
        raise ValueError("decision_lead_ns must be positive")
    if assumed_binance_latency_ns < 0 or assumed_onchain_latency_ns < 0:
        raise ValueError("availability latency assumptions must be non-negative")
    if prediction_interval_seconds is not None and prediction_interval_seconds <= 0:
        raise ValueError("prediction_interval_seconds must be positive when supplied")
    if subgraph_availability_latency_ns is not None and subgraph_availability_latency_ns < 0:
        raise ValueError("subgraph_availability_latency_ns must be non-negative when supplied")
    if not result.examples:
        raise ValueError("historical dataset artifact requires at least one example")
    if not result.feature_names:
        raise ValueError("feature_names are required")
    if len(set(result.feature_names)) != len(result.feature_names):
        raise ValueError("feature_names must be unique")

    examples = [_example_payload(example) for example in result.examples]
    round_ids = [int(row["round_id"]) for row in examples]
    if len(set(round_ids)) != len(round_ids):
        raise ValueError("round IDs must be unique in dataset artifact")
    if any(current <= previous for previous, current in zip(round_ids, round_ids[1:])):
        raise ValueError("examples must be strictly ordered by round_id")

    expected_names = tuple(result.feature_names)
    for row in examples:
        names = tuple(str(pair[0]) for pair in row["features"])
        if names != expected_names:
            raise ValueError(
                f"feature schema mismatch for round {row['round_id']}: {names} != {expected_names}"
            )

    assumptions: dict[str, Any] = {
        "decision_lead_ns": decision_lead_ns,
        "binance_availability_latency_ns": assumed_binance_latency_ns,
        "onchain_availability_latency_ns": assumed_onchain_latency_ns,
    }
    if prediction_interval_seconds is not None:
        assumptions["prediction_interval_seconds"] = prediction_interval_seconds
    if subgraph_availability_latency_ns is not None:
        assumptions["subgraph_availability_latency_ns"] = subgraph_availability_latency_ns

    payload: dict[str, Any] = {
        "schema": DATASET_ARTIFACT_SCHEMA,
        "dataset_id": dataset_id,
        "generated_at_ns": generated_at_ns,
        "source_event_store": {
            "availability_mode": store.mode,
            "tip_hash": _store_tip(store),
            "event_count": len(store.read_all_ingest_order()),
        },
        "assumptions": assumptions,
        "feature_policy": _feature_policy_payload(feature_policy),
        "feature_names": list(expected_names),
        "examples": examples,
        "skipped": [
            {"epoch": skip.epoch, "reason": skip.reason}
            for skip in result.skipped
        ],
    }
    digest = hashlib.sha256(_canonical_payload(payload)).hexdigest()
    artifact = HistoricalDatasetArtifact(digest, payload)
    artifact.validate()
    return artifact


def _validate_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("schema") != DATASET_ARTIFACT_SCHEMA:
        raise ValueError("unsupported historical dataset artifact schema")
    if payload.get("source_event_store", {}).get("availability_mode") != "reconstructed":
        raise ValueError("dataset artifact source mode must be reconstructed")
    assumptions = payload.get("assumptions")
    if not isinstance(assumptions, Mapping):
        raise ValueError("dataset artifact assumptions are invalid")
    interval = assumptions.get("prediction_interval_seconds")
    if interval is not None:
        if isinstance(interval, bool) or not isinstance(interval, int) or interval <= 0:
            raise ValueError("dataset artifact Prediction interval is invalid")
    subgraph_latency = assumptions.get("subgraph_availability_latency_ns")
    if subgraph_latency is not None:
        if isinstance(subgraph_latency, bool) or not isinstance(subgraph_latency, int) or subgraph_latency < 0:
            raise ValueError("dataset artifact subgraph latency is invalid")
    feature_names = payload.get("feature_names")
    if not isinstance(feature_names, list) or not feature_names:
        raise ValueError("dataset artifact feature_names are invalid")
    names = tuple(str(value) for value in feature_names)
    if len(set(names)) != len(names):
        raise ValueError("dataset artifact feature_names must be unique")
    examples_raw = payload.get("examples")
    if not isinstance(examples_raw, list) or not examples_raw:
        raise ValueError("dataset artifact examples are invalid")
    examples = _decode_examples(examples_raw)
    round_ids = tuple(example.round_id for example in examples)
    if len(set(round_ids)) != len(round_ids):
        raise ValueError("dataset artifact contains duplicate round IDs")
    if any(current <= previous for previous, current in zip(round_ids, round_ids[1:])):
        raise ValueError("dataset artifact round IDs are not strictly ordered")
    for example in examples:
        if tuple(name for name, _ in example.features) != names:
            raise ValueError(f"dataset artifact feature schema mismatch in round {example.round_id}")
        if example.label_available_at_ns <= example.decision_cutoff_ns:
            raise ValueError("dataset artifact contains future-leaking label availability")


def _decode_examples(rows: Any) -> tuple[TrainingExample, ...]:
    if not isinstance(rows, list):
        raise ValueError("dataset artifact examples must be a list")
    result: list[TrainingExample] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("dataset artifact example must be an object")
        feature_rows = row.get("features")
        if not isinstance(feature_rows, list):
            raise ValueError("dataset artifact features must be a list")
        features: list[tuple[str, float]] = []
        for pair in feature_rows:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("dataset artifact feature entry must be [name, value]")
            features.append((str(pair[0]), float(pair[1])))
        try:
            outcome = Outcome(str(row["outcome"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("dataset artifact outcome is invalid") from exc
        result.append(
            TrainingExample(
                round_id=int(row["round_id"]),
                decision_cutoff_ns=int(row["decision_cutoff_ns"]),
                label_available_at_ns=int(row["label_available_at_ns"]),
                features=tuple(features),
                outcome=outcome,
            )
        )
    return tuple(result)


def load_historical_dataset_artifact(path: str | Path) -> HistoricalDatasetArtifact:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("historical dataset artifact could not be read") from exc
    if not isinstance(document, dict):
        raise ValueError("historical dataset artifact must be an object")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("historical dataset artifact payload is invalid")
    artifact = HistoricalDatasetArtifact(
        artifact_sha256=str(document.get("artifact_sha256", "")),
        payload=payload,
    )
    artifact.validate()
    return artifact
