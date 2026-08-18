from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pancake_prediction import clickhouse_cli
from pancake_prediction.clickhouse import BinanceArchiveIngestReport


class FakeClient:
    def execute(self, query: str) -> str:
        assert query == "SELECT 1"
        return "1\n"

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
class FakeTrade:
    aggregate_trade_id: int


@dataclass(frozen=True, slots=True)
class FakeCanonicalInputs:
    replay: object
    events: tuple[object, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "market": "BNBUSD",
            "replay_rounds": 123,
            "replay_input_digest": "a" * 64,
            "replay_output_digest": "b" * 64,
            "active_chainlink_event_count": 456,
        }


@dataclass(frozen=True, slots=True)
class FakeDatasetResult:
    def as_dict(self) -> dict[str, object]:
        return {
            "market": "BNBUSD",
            "candidate_rounds": 123,
            "research_feature_rows": 100,
            "chunk_span_ms": 3_600_000,
            "chunks_loaded": 12,
            "max_spot_chunk_rows": 50_000,
            "max_perp_chunk_rows": 40_000,
            "spot_query_start_ms": 1_000,
            "perp_query_start_ms": None,
            "query_end_ms": 2_000,
        }


@dataclass(frozen=True, slots=True)
class FakeCampaignManifest:
    def as_dict(self) -> dict[str, object]:
        return {
            "campaign_digest": "c" * 64,
            "spot_sources": [{"source_sha256": "d" * 64}],
            "perp_sources": [],
        }


def test_clickhouse_cli_requires_environment_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLICKHOUSE_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        clickhouse_cli.main(["ping"])
    assert exc_info.value.code == 2


def test_clickhouse_cli_ping_does_not_print_connection_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "https://example.invalid")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "super-secret")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClient(),
    )
    assert clickhouse_cli.main(["ping"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {"ok": True}
    assert "super-secret" not in output
    assert "example.invalid" not in output


def test_clickhouse_cli_schema_check_reports_ready(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClient(),
    )
    assert clickhouse_cli.main(["schema-check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["engine"] == "ReplacingMergeTree"
    assert payload["engine_version_ready"] is True
    assert payload["sorting_key_ready"] is True


def test_clickhouse_cli_binance_ingest_passes_explicit_latency_and_batching(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClient(),
    )

    archive = tmp_path / "x.zip"
    checksum = tmp_path / "x.zip.CHECKSUM"
    archive.write_bytes(b"unused")
    checksum.write_text("unused", encoding="utf-8")

    def fake_ingest(
        sink: object,
        archive_path: Path,
        checksum_path: Path,
        **kwargs: object,
    ) -> BinanceArchiveIngestReport:
        assert isinstance(sink, FakeClient)
        assert archive_path == archive
        assert checksum_path == checksum
        assert kwargs["market"] == "BNBUSD"
        assert kwargs["venue"] == "spot"
        assert kwargs["timestamp_unit"] == "auto"
        assert kwargs["availability_lag_ms"] == 25
        assert kwargs["batch_size"] == 2_000
        return BinanceArchiveIngestReport(
            market="BNBUSD",
            venue="spot",
            symbol="BNBUSDT",
            source_name=archive.name,
            source_sha256="a" * 64,
            timestamp_unit="auto",
            availability_lag_ms=25,
            ingest_version=1,
            batches=2,
            rows=3,
            first_aggregate_trade_id=1,
            last_aggregate_trade_id=3,
            first_trade_timestamp_ms=100,
            last_trade_timestamp_ms=102,
        )

    monkeypatch.setattr(clickhouse_cli, "ingest_binance_archive", fake_ingest)
    args = [
        "binance-ingest",
        "--market",
        "BNBUSD",
        "--archive",
        str(archive),
        "--checksum",
        str(checksum),
        "--venue",
        "spot",
        "--availability-lag-ms",
        "25",
        "--batch-size",
        "2000",
    ]
    assert clickhouse_cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"] == 3
    assert payload["availability_lag_ms"] == 25


def test_clickhouse_cli_window_reports_only_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        clickhouse_cli,
        "load_binance_trade_window",
        lambda *args, **kwargs: (FakeTrade(10), FakeTrade(11)),
    )
    args = [
        "binance-window",
        "--market",
        "BNBUSD",
        "--venue",
        "spot",
        "--timestamp-unit",
        "auto",
        "--availability-lag-ms",
        "25",
        "--start-ms",
        "1000",
        "--end-ms",
        "2000",
    ]
    assert clickhouse_cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "market": "BNBUSD",
        "venue": "spot",
        "timestamp_unit": "auto",
        "rows": 2,
        "first_aggregate_trade_id": 10,
        "last_aggregate_trade_id": 11,
    }


def test_clickhouse_cli_dataset_summary_binds_canonical_inputs_and_assumptions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "dataset-secret")
    monkeypatch.setattr(
        clickhouse_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClient(),
    )
    replay = object()
    events: tuple[object, ...] = ()
    inputs = FakeCanonicalInputs(replay=replay, events=events)
    database = tmp_path / "history.sqlite3"

    def fake_load(path: Path, market: str) -> FakeCanonicalInputs:
        assert path == database
        assert market == "BNBUSD"
        return inputs

    def fake_build(
        received_replay: object,
        received_events: tuple[object, ...],
        source: object,
        **kwargs: object,
    ) -> FakeDatasetResult:
        assert received_replay is replay
        assert received_events == events
        assert isinstance(source, FakeClient)
        assert kwargs["spot_timestamp_unit"] == "auto"
        assert kwargs["spot_availability_lag_ms"] == 25
        assert kwargs["perp_timestamp_unit"] == "milliseconds"
        assert kwargs["perp_availability_lag_ms"] == 40
        assert kwargs["include_perp"] is False
        assert kwargs["chunk_span_ms"] == 3_600_000
        assert kwargs["chainlink_availability_lag_ms"] == 500
        return FakeDatasetResult()

    def fake_manifest(
        source: object,
        received_inputs: object,
        dataset: object,
        assumptions: object,
        **kwargs: object,
    ) -> FakeCampaignManifest:
        assert isinstance(source, FakeClient)
        assert received_inputs is inputs
        assert isinstance(dataset, FakeDatasetResult)
        assert isinstance(assumptions, dict)
        assert kwargs["spot_timestamp_unit"] == "auto"
        assert kwargs["spot_availability_lag_ms"] == 25
        assert kwargs["perp_timestamp_unit"] == "milliseconds"
        assert kwargs["perp_availability_lag_ms"] == 40
        assert kwargs["include_perp"] is False
        return FakeCampaignManifest()

    monkeypatch.setattr(clickhouse_cli, "load_canonical_research_inputs", fake_load)
    monkeypatch.setattr(
        clickhouse_cli,
        "build_chunked_clickhouse_research_dataset",
        fake_build,
    )
    monkeypatch.setattr(clickhouse_cli, "build_clickhouse_campaign_manifest", fake_manifest)
    args = [
        "dataset-summary",
        "--market",
        "BNBUSD",
        "--db",
        str(database),
        "--spot-availability-lag-ms",
        "25",
        "--perp-availability-lag-ms",
        "40",
        "--no-perp",
        "--chainlink-availability-lag-ms",
        "500",
    ]
    assert clickhouse_cli.main(args) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["inputs"]["replay_rounds"] == 123
    assert payload["assumptions"]["spot_timestamp_unit"] == "auto"
    assert payload["assumptions"]["spot_availability_lag_ms"] == 25
    assert payload["assumptions"]["perp_timestamp_unit"] == "milliseconds"
    assert payload["assumptions"]["perp_availability_lag_ms"] == 40
    assert payload["assumptions"]["include_perp"] is False
    assert payload["assumptions"]["chainlink_availability_lag_ms"] == 500
    assert payload["dataset"]["research_feature_rows"] == 100
    assert payload["dataset"]["chunks_loaded"] == 12
    assert payload["campaign_manifest"]["campaign_digest"] == "c" * 64
    assert payload["campaign_manifest"]["spot_sources"][0]["source_sha256"] == "d" * 64
    assert "dataset-secret" not in output
