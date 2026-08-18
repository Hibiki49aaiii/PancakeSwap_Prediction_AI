from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pancake_prediction import clickhouse_cli
from pancake_prediction.campaign_evaluation import EconomicCampaignConfig


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
    replay = object()
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
