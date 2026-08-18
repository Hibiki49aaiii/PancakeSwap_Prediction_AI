from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from pancake_prediction.clickhouse import (
    ClickHouseHttpClient,
    QueryParameter,
    ingest_binance_archive,
    load_binance_trade_window,
)


class RecordingClient(ClickHouseHttpClient):
    def __init__(self, response: bytes = b"") -> None:
        super().__init__("http://127.0.0.1:8123")
        self.calls: list[
            tuple[str, bytes, Mapping[str, QueryParameter] | None]
        ] = []
        self.response = response

    def _post(
        self,
        query: str,
        data: bytes = b"",
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> bytes:
        self.calls.append((query, data, parameters))
        return self.response


class RecordingSource:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self.rows = rows
        self.query = ""
        self.parameters: Mapping[str, QueryParameter] | None = None

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        self.query = query
        self.parameters = parameters
        yield from self.rows


def _archive(tmp_path: Path) -> tuple[Path, Path, str]:
    archive_path = tmp_path / "BNBUSDT-aggTrades-2026-08-01.zip"
    payload = (
        b"1,600.0,1.0,10,10,1785542400000000,false,true\n"
        b"2,601.0,2.0,11,12,1785542400001000,true,true\n"
        b"3,602.0,3.0,13,13,1785542400002000,false,true\n"
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("BNBUSDT-aggTrades-2026-08-01.csv", payload)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = tmp_path / (archive_path.name + ".CHECKSUM")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, checksum_path, digest


def test_clickhouse_client_rejects_credentials_in_endpoint() -> None:
    with pytest.raises(ValueError, match="credentials"):
        ClickHouseHttpClient("https://user:secret@example.invalid")


def test_clickhouse_insert_batches_json_each_row_without_full_archive_materialization() -> None:
    client = RecordingClient()
    rows = ({"value": index} for index in range(5))
    report = client.insert_json_rows("binance_agg_trades", rows, batch_size=2)
    assert report.rows == 5
    assert report.batches == 3
    assert [len(data.splitlines()) for _, data, _ in client.calls] == [2, 2, 1]
    assert all(query.endswith("FORMAT JSONEachRow") for query, _, _ in client.calls)


def test_ingest_binance_archive_verifies_checksum_and_persists_provenance(
    tmp_path: Path,
) -> None:
    archive_path, checksum_path, digest = _archive(tmp_path)
    client = RecordingClient()
    report = ingest_binance_archive(
        client,
        archive_path,
        checksum_path,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="auto",
        availability_lag_ms=25,
        batch_size=2,
        ingest_version=123,
    )
    assert report.rows == 3
    assert report.batches == 2
    assert report.source_sha256 == digest
    assert report.first_aggregate_trade_id == 1
    assert report.last_aggregate_trade_id == 3
    assert report.first_trade_timestamp_ms == 1_785_542_400_000
    assert report.last_trade_timestamp_ms == 1_785_542_400_002

    rows = [
        json.loads(line)
        for _, payload, _ in client.calls
        for line in payload.splitlines()
    ]
    assert len(rows) == 3
    assert all(row["source_sha256"] == digest for row in rows)
    assert all(row["availability_lag_ms"] == 25 for row in rows)
    assert all(row["ingest_version"] == 123 for row in rows)
    assert rows[0]["event_timestamp_ms"] == 1_785_542_400_025


def test_ingest_rejects_bad_checksum_before_any_clickhouse_insert(tmp_path: Path) -> None:
    archive_path, checksum_path, _digest = _archive(tmp_path)
    checksum_path.write_text("0" * 64 + f"  {archive_path.name}\n", encoding="utf-8")
    client = RecordingClient()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        ingest_binance_archive(
            client,
            archive_path,
            checksum_path,
            market="BNBUSD",
            venue="spot",
            timestamp_unit="auto",
            availability_lag_ms=0,
        )
    assert client.calls == []


def test_trade_window_query_uses_final_and_typed_parameters() -> None:
    source = RecordingSource(
        (
            {
                "symbol": "BNBUSDT",
                "event_timestamp_ms": 10_025,
                "trade_timestamp_ms": 10_000,
                "price_e8": 60_000_000_000,
                "quantity_e8": 100_000_000,
                "aggressive_side": "buy",
                "aggregate_trade_id": 42,
            },
        )
    )
    trades = load_binance_trade_window(
        source,
        market="BNBUSD",
        venue="spot",
        availability_lag_ms=25,
        start_timestamp_ms=9_000,
        end_timestamp_ms=11_000,
    )
    assert len(trades) == 1
    assert trades[0].aggregate_trade_id == 42
    assert "FROM binance_agg_trades FINAL" in source.query
    assert "availability_lag_ms={availability_lag_ms:UInt32}" in source.query
    assert source.parameters == {
        "venue": "spot",
        "symbol": "BNBUSDT",
        "availability_lag_ms": 25,
        "start_timestamp_ms": 9_000,
        "end_timestamp_ms": 11_000,
    }
