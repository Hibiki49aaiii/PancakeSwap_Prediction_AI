from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from pancake_prediction import clickhouse_cli
from pancake_prediction.clickhouse import BinanceArchiveIngestReport


class FakeClient:
    def execute(self, query: str) -> str:
        assert query == "SELECT 1"
        return "1\n"


@dataclass(frozen=True, slots=True)
class FakeTrade:
    aggregate_trade_id: int


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
        "rows": 2,
        "first_aggregate_trade_id": 10,
        "last_aggregate_trade_id": 11,
    }
