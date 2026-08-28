from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pancake_prediction import clickhouse_cli
from pancake_prediction.campaign_evaluation import EconomicCampaignConfig
from pancake_prediction.replay import ReplaySnapshot
from pancake_prediction.research_ledger import ResearchPredictionRecord
from pancake_prediction.shadow_inference import (
    ShadowInferenceConfig,
    ShadowTargetSelection,
)


class ReadyClient:
    def query_json_rows(self, query: str) -> Iterator[dict[str, object]]:
        if "system.tables" in query:
            yield {
                "engine": "ReplacingMergeTree",
                "engine_full": "ReplacingMergeTree(ingest_version)",
                "sorting_key": (
                    "venue, symbol, timestamp_unit, availability_lag_ms, "
                    "aggregate_trade_id"
                ),
            }
            return
        if "system.columns" in query:
            columns = {
                "venue": "LowCardinality(String)",
                "symbol": "LowCardinality(String)",
                "timestamp_unit": "LowCardinality(String)",
                "event_timestamp_ms": "UInt64",
                "trade_timestamp_ms": "UInt64",
                "aggregate_trade_id": "UInt64",
                "price_e8": "UInt64",
                "quantity_e8": "UInt64",
                "source_sha256": "FixedString(64)",
                "source_name": "String",
                "availability_lag_ms": "UInt32",
                "ingest_version": "UInt64",
            }
            for name, value_type in columns.items():
                yield {"name": name, "type": value_type}
            return
        raise AssertionError(f"unexpected query: {query}")


@dataclass(frozen=True, slots=True)
class FakeInputs:
    replay: object
    events: tuple[object, ...]

    def as_dict(self) -> dict[str, object]:
        return {"market": "BNBUSD", "replay_rounds": 1}


@dataclass(frozen=True, slots=True)
class FakeResearchDataset:
    research_feature_rows: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class FakeDatasetResult:
    dataset: FakeResearchDataset

    def as_dict(self) -> dict[str, object]:
        return {"research_feature_rows": len(self.dataset.research_feature_rows)}


@dataclass(frozen=True, slots=True)
class FakeManifest:
    digest: str = "c" * 64

    def as_dict(self) -> dict[str, object]:
        return {"campaign_digest": self.digest}


@dataclass(frozen=True, slots=True)
class FakeBundle:
    inputs: FakeInputs
    assumptions: dict[str, object]
    dataset: FakeDatasetResult
    manifest: FakeManifest


@dataclass(frozen=True, slots=True)
class FakeShadowInference:
    prediction: ResearchPredictionRecord
    config: ShadowInferenceConfig

    def as_dict(self) -> dict[str, object]:
        return {
            "prediction": self.prediction.canonical_payload(),
            "prediction_digest": self.prediction.digest(),
            "training_row_count": self.config.min_train_rounds,
            "signing_enabled": False,
            "live_broadcast": False,
        }


@dataclass(frozen=True, slots=True)
class FakeLiveSyncReport:
    market: str
    venue: str
    rows: int

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "venue": self.venue,
            "rows": self.rows,
            "response_chain_sha256": "e" * 64,
        }


@dataclass(frozen=True, slots=True)
class FakeEvaluation:
    config: EconomicCampaignConfig

    def as_dict(self) -> dict[str, object]:
        return {
            "campaign_digest": "c" * 64,
            "evaluation_digest": "d" * 64,
            "config": {
                "stake_wei": self.config.stake_wei,
                "bet_gas_wei": self.config.bet_gas_wei,
                "claim_gas_wei": self.config.claim_gas_wei,
                "inclusion_latency_seconds": self.config.inclusion_latency_seconds,
                "decision_lead_seconds": self.config.decision_lead_seconds,
                "run_ablation": self.config.run_ablation,
            },
        }


def _dataset_args() -> list[str]:
    return [
        "--market",
        "BNBUSD",
        "--db",
        "history.sqlite3",
        "--spot-availability-lag-ms",
        "25",
    ]


def test_campaign_evaluate_requires_explicit_cost_and_latency_arguments() -> None:
    with pytest.raises(SystemExit) as exc_info:
        clickhouse_cli.build_parser().parse_args(
            ["campaign-evaluate", *_dataset_args()]
        )
    assert exc_info.value.code == 2


def test_campaign_evaluate_binds_manifest_and_explicit_economic_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "campaign-secret")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: ReadyClient(),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(),
    )
    events: tuple[object, ...] = ()
    rows: tuple[object, ...] = (object(),)
    bundle = FakeBundle(
        inputs=FakeInputs(replay=replay, events=events),
        assumptions={"spot_availability_lag_ms": 25},
        dataset=FakeDatasetResult(FakeResearchDataset(rows)),
        manifest=FakeManifest(),
    )
    database = tmp_path / "history.sqlite3"

    def fake_bundle(args: argparse.Namespace, client: object) -> FakeBundle:
        assert isinstance(client, ReadyClient)
        assert args.db == database
        return bundle

    captured: dict[str, object] = {}

    def fake_evaluate(
        received_replay: object,
        received_events: tuple[object, ...],
        received_rows: tuple[object, ...],
        *,
        campaign_digest: str,
        config: EconomicCampaignConfig,
        feature_set_id: str = "full-v1",
    ) -> FakeEvaluation:
        assert received_replay is replay
        assert received_events == events
        assert received_rows == rows
        assert campaign_digest == "c" * 64
        assert feature_set_id == "full-v1"
        captured["config"] = config
        return FakeEvaluation(config)

    monkeypatch.setattr(clickhouse_cli, "_build_dataset_bundle", fake_bundle)
    monkeypatch.setattr(
        clickhouse_cli,
        "run_source_bound_economic_campaign",
        fake_evaluate,
    )
    args = [
        "campaign-evaluate",
        "--market",
        "BNBUSD",
        "--db",
        str(database),
        "--spot-availability-lag-ms",
        "25",
        "--feature-lead-seconds",
        "17",
        "--stake-wei",
        "1000000000000000",
        "--bet-gas-wei",
        "120000000000000",
        "--claim-gas-wei",
        "90000000000000",
        "--inclusion-latency-seconds",
        "3",
        "--min-train-rounds",
        "500",
        "--test-rounds",
        "100",
        "--purge-rounds",
        "3",
        "--run-ablation",
    ]
    assert clickhouse_cli.main(args) == 0
    config = captured["config"]
    assert isinstance(config, EconomicCampaignConfig)
    assert config.stake_wei == 10**15
    assert config.bet_gas_wei == 120_000_000_000_000
    assert config.claim_gas_wei == 90_000_000_000_000
    assert config.inclusion_latency_seconds == 3
    assert config.decision_lead_seconds == 17
    assert config.min_train_rounds == 500
    assert config.test_rounds == 100
    assert config.purge_rounds == 3
    assert config.run_ablation is True

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["campaign_manifest"]["campaign_digest"] == "c" * 64
    assert payload["evaluation"]["campaign_digest"] == "c" * 64
    assert payload["evaluation"]["evaluation_digest"] == "d" * 64
    assert payload["evaluation"]["config"]["decision_lead_seconds"] == 17
    assert "campaign-secret" not in output

def test_shadow_infer_binds_target_costs_and_appends_ledger(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: ReadyClient(),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(),
    )
    events: tuple[object, ...] = ()
    rows: tuple[object, ...] = (object(),)
    bundle = FakeBundle(
        inputs=FakeInputs(replay=replay, events=events),
        assumptions={"spot_availability_lag_ms": 25},
        dataset=FakeDatasetResult(FakeResearchDataset(rows)),
        manifest=FakeManifest(),
    )
    database = tmp_path / "history.sqlite3"
    shadow_database = tmp_path / "shadow.sqlite3"

    def fake_bundle(args: argparse.Namespace, client: object) -> FakeBundle:
        assert isinstance(client, ReadyClient)
        assert args.db == database
        return bundle

    captured: dict[str, object] = {}

    def fake_shadow(
        received_replay: object,
        received_events: tuple[object, ...],
        received_rows: tuple[object, ...],
        *,
        target_epoch: int,
        config: ShadowInferenceConfig,
    ) -> FakeShadowInference:
        assert received_replay is replay
        assert received_events == events
        assert received_rows == rows
        assert target_epoch == 123
        captured["config"] = config
        prediction = ResearchPredictionRecord(
            market="BNBUSD",
            epoch=123,
            decision_timestamp_ms=123_280_000,
            model_id="shadow-test-model",
            feature_set_id="full-v1",
            raw_probability_ppm=620_000,
            calibrated_probability_ppm=600_000,
            expected_value_wei=1234,
            action="bull",
            feature_digest="a" * 64,
            train_max_epoch=120,
            metadata={"source": "test"},
        )
        return FakeShadowInference(prediction=prediction, config=config)

    monkeypatch.setattr(clickhouse_cli, "_build_dataset_bundle", fake_bundle)
    monkeypatch.setattr(clickhouse_cli, "build_shadow_inference", fake_shadow)

    args = [
        "shadow-infer",
        "--market",
        "BNBUSD",
        "--db",
        str(database),
        "--spot-availability-lag-ms",
        "25",
        "--feature-lead-seconds",
        "17",
        "--target-epoch",
        "123",
        "--shadow-db",
        str(shadow_database),
        "--stake-wei",
        "1000000000000000",
        "--bet-gas-wei",
        "120000000000000",
        "--claim-gas-wei",
        "90000000000000",
        "--inclusion-latency-seconds",
        "3",
        "--min-train-rounds",
        "50",
        "--calibration-rounds",
        "10",
        "--purge-rounds",
        "2",
        "--pool-min-train-rounds",
        "20",
        "--pool-window-rounds",
        "40",
    ]
    assert clickhouse_cli.main(args) == 0

    config = captured["config"]
    assert isinstance(config, ShadowInferenceConfig)
    assert config.stake_wei == 10**15
    assert config.bet_gas_wei == 120_000_000_000_000
    assert config.claim_gas_wei == 90_000_000_000_000
    assert config.inclusion_latency_seconds == 3
    assert config.decision_lead_seconds == 17
    assert config.min_train_rounds == 50
    assert config.calibration_rounds == 10
    assert config.purge_rounds == 2
    assert config.pool_min_train_rounds == 20
    assert config.pool_window_rounds == 40

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["shadow_reconciliation"]["prediction_count"] == 0
    assert payload["shadow_reconciliation"]["appended_settlement_count"] == 0
    assert payload["shadow_inference"]["prediction"]["epoch"] == 123
    assert payload["shadow_ledger_event"]["kind"] == "prediction"
    assert payload["shadow_ledger_event"]["sequence"] == 1
    assert payload["shadow_inference"]["signing_enabled"] is False
    assert payload["shadow_inference"]["live_broadcast"] is False

    # The same deterministic prediction is idempotent across retries.
    assert clickhouse_cli.main(args) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["shadow_reconciliation"]["prediction_count"] == 1
    assert repeated["shadow_reconciliation"]["unresolved_count"] == 1
    assert repeated["shadow_ledger_event"]["sequence"] == 1
    assert (
        repeated["shadow_ledger_event"]["event_digest"]
        == payload["shadow_ledger_event"]["event_digest"]
    )

def test_shadow_infer_auto_selects_target_with_explicit_clock_override(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: ReadyClient(),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(),
    )
    rows: tuple[object, ...] = (object(),)
    bundle = FakeBundle(
        inputs=FakeInputs(replay=replay, events=()),
        assumptions={"spot_availability_lag_ms": 25},
        dataset=FakeDatasetResult(FakeResearchDataset(rows)),
        manifest=FakeManifest(),
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "_build_dataset_bundle",
        lambda args, client: bundle,
    )

    captured: dict[str, object] = {}

    def fake_select(
        received_replay: object,
        received_events: tuple[object, ...],
        *,
        now_timestamp: int,
        config: ShadowInferenceConfig,
    ) -> ShadowTargetSelection:
        assert received_replay is replay
        assert received_events == ()
        assert now_timestamp == 1_700_000_000
        captured["selection_config"] = config
        return ShadowTargetSelection(
            epoch=123,
            decision_timestamp=1_700_000_000,
            scheduled_lock_timestamp=1_700_000_020,
            latest_submission_timestamp=1_700_000_017,
        )

    def fake_shadow(
        received_replay: object,
        received_events: tuple[object, ...],
        received_rows: tuple[object, ...],
        *,
        target_epoch: int,
        config: ShadowInferenceConfig,
    ) -> FakeShadowInference:
        assert received_replay is replay
        assert received_events == ()
        assert received_rows == rows
        assert target_epoch == 123
        captured["inference_config"] = config
        return FakeShadowInference(
            prediction=ResearchPredictionRecord(
                market="BNBUSD",
                epoch=123,
                decision_timestamp_ms=1_700_000_000_000,
                model_id="shadow-auto-test",
                feature_set_id="full-v1",
                raw_probability_ppm=600_000,
                calibrated_probability_ppm=590_000,
                expected_value_wei=10,
                action="bull",
                feature_digest="a" * 64,
                train_max_epoch=120,
                metadata={"source": "test"},
            ),
            config=config,
        )

    monkeypatch.setattr(clickhouse_cli, "select_shadow_target", fake_select)
    monkeypatch.setattr(clickhouse_cli, "build_shadow_inference", fake_shadow)

    args = [
        "shadow-infer",
        "--market",
        "BNBUSD",
        "--db",
        str(tmp_path / "history.sqlite3"),
        "--spot-availability-lag-ms",
        "25",
        "--feature-lead-seconds",
        "20",
        "--now-timestamp",
        "1700000000",
        "--shadow-db",
        str(tmp_path / "shadow.sqlite3"),
        "--stake-wei",
        "100",
        "--bet-gas-wei",
        "2",
        "--claim-gas-wei",
        "1",
        "--inclusion-latency-seconds",
        "3",
        "--min-train-rounds",
        "50",
        "--calibration-rounds",
        "10",
        "--pool-min-train-rounds",
        "20",
        "--pool-window-rounds",
        "40",
    ]
    assert clickhouse_cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["shadow_cycle"]["status"] == "prediction_recorded"
    assert payload["shadow_cycle"]["explicit_target"] is False
    assert payload["shadow_cycle"]["selection"]["epoch"] == 123
    assert payload["shadow_ledger_event"]["kind"] == "prediction"
    assert captured["selection_config"] is captured["inference_config"]


def test_shadow_infer_auto_cycle_is_noop_outside_decision_window(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: ReadyClient(),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(),
    )
    bundle = FakeBundle(
        inputs=FakeInputs(replay=replay, events=()),
        assumptions={"spot_availability_lag_ms": 25},
        dataset=FakeDatasetResult(FakeResearchDataset(())),
        manifest=FakeManifest(),
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "_build_dataset_bundle",
        lambda args, client: bundle,
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "select_shadow_target",
        lambda *args, **kwargs: None,
    )

    def should_not_infer(*args: object, **kwargs: object) -> object:
        raise AssertionError("inference must not run without an eligible target")

    monkeypatch.setattr(
        clickhouse_cli,
        "build_shadow_inference",
        should_not_infer,
    )

    args = [
        "shadow-infer",
        "--market",
        "BNBUSD",
        "--db",
        str(tmp_path / "history.sqlite3"),
        "--spot-availability-lag-ms",
        "25",
        "--now-timestamp",
        "1700000000",
        "--shadow-db",
        str(tmp_path / "shadow.sqlite3"),
        "--stake-wei",
        "100",
        "--bet-gas-wei",
        "2",
        "--claim-gas-wei",
        "1",
        "--inclusion-latency-seconds",
        "2",
    ]
    assert clickhouse_cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["shadow_cycle"]["status"] == "no_eligible_target"
    assert payload["shadow_cycle"]["now_timestamp"] == 1_700_000_000
    assert payload["shadow_reconciliation"]["prediction_count"] == 0

def test_binance_live_sync_cli_binds_prospective_collection_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "do-not-print-live-secret")
    client = ReadyClient()
    rest_source = object()
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "BinancePublicHttpClient",
        lambda: rest_source,
    )
    captured: dict[str, object] = {}

    def fake_live_sync(
        sink: object,
        cursor_source: object,
        source: object,
        *,
        market: str,
        venue: str,
        availability_lag_ms: int,
        timestamp_unit: str,
        now_timestamp_ms: int | None,
        bootstrap_window_ms: int,
        batch_size: int,
        max_pages: int,
    ) -> FakeLiveSyncReport:
        assert sink is client
        assert cursor_source is client
        assert source is rest_source
        captured.update(
            {
                "market": market,
                "venue": venue,
                "availability_lag_ms": availability_lag_ms,
                "timestamp_unit": timestamp_unit,
                "now_timestamp_ms": now_timestamp_ms,
                "bootstrap_window_ms": bootstrap_window_ms,
                "batch_size": batch_size,
                "max_pages": max_pages,
            }
        )
        return FakeLiveSyncReport(market=market, venue=venue, rows=17)

    monkeypatch.setattr(
        clickhouse_cli,
        "sync_binance_live_aggtrades",
        fake_live_sync,
    )

    assert (
        clickhouse_cli.main(
            [
                "binance-live-sync",
                "--market",
                "BNBUSD",
                "--venue",
                "um_futures",
                "--availability-lag-ms",
                "250",
                "--timestamp-unit",
                "auto",
                "--bootstrap-window-ms",
                "90000",
                "--batch-size",
                "2000",
                "--max-pages",
                "12",
                "--now-timestamp-ms",
                "1700000000123",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["market"] == "BNBUSD"
    assert payload["venue"] == "um_futures"
    assert payload["rows"] == 17
    assert captured == {
        "market": "BNBUSD",
        "venue": "um_futures",
        "availability_lag_ms": 250,
        "timestamp_unit": "auto",
        "now_timestamp_ms": 1_700_000_000_123,
        "bootstrap_window_ms": 90_000,
        "batch_size": 2_000,
        "max_pages": 12,
    }
    assert "do-not-print-live-secret" not in json.dumps(payload)

def test_shadow_infer_auto_cycle_refuses_prediction_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: ReadyClient(),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=(),
    )
    rows: tuple[object, ...] = (object(),)
    bundle = FakeBundle(
        inputs=FakeInputs(replay=replay, events=()),
        assumptions={"spot_availability_lag_ms": 25},
        dataset=FakeDatasetResult(FakeResearchDataset(rows)),
        manifest=FakeManifest(),
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "_build_dataset_bundle",
        lambda args, client: bundle,
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "select_shadow_target",
        lambda *args, **kwargs: ShadowTargetSelection(
            epoch=123,
            decision_timestamp=1_000,
            scheduled_lock_timestamp=1_020,
            latest_submission_timestamp=1_018,
        ),
    )

    def fake_shadow(
        received_replay: object,
        received_events: tuple[object, ...],
        received_rows: tuple[object, ...],
        *,
        target_epoch: int,
        config: ShadowInferenceConfig,
    ) -> FakeShadowInference:
        return FakeShadowInference(
            prediction=ResearchPredictionRecord(
                market="BNBUSD",
                epoch=target_epoch,
                decision_timestamp_ms=1_000_000,
                model_id="shadow-deadline-test",
                feature_set_id="full-v1",
                raw_probability_ppm=600_000,
                calibrated_probability_ppm=590_000,
                expected_value_wei=10,
                action="bull",
                feature_digest="a" * 64,
                train_max_epoch=120,
                metadata={"source": "test"},
            ),
            config=config,
        )

    monkeypatch.setattr(clickhouse_cli, "build_shadow_inference", fake_shadow)
    timestamps = iter((1_000.0, 1_018.0))
    monkeypatch.setattr(clickhouse_cli.time, "time", lambda: next(timestamps))

    shadow_db = tmp_path / "shadow.sqlite3"
    args = [
        "shadow-infer",
        "--market",
        "BNBUSD",
        "--db",
        str(tmp_path / "history.sqlite3"),
        "--spot-availability-lag-ms",
        "25",
        "--feature-lead-seconds",
        "20",
        "--shadow-db",
        str(shadow_db),
        "--stake-wei",
        "100",
        "--bet-gas-wei",
        "2",
        "--claim-gas-wei",
        "1",
        "--inclusion-latency-seconds",
        "2",
        "--min-train-rounds",
        "50",
        "--calibration-rounds",
        "10",
        "--pool-min-train-rounds",
        "20",
        "--pool-window-rounds",
        "40",
    ]
    assert clickhouse_cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["shadow_cycle"]["status"] == "missed_submission_deadline"
    assert payload["shadow_cycle"]["now_timestamp"] == 1_018
    assert "shadow_ledger_event" not in payload

