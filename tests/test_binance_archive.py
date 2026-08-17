from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from pancake_prediction.binance_archive import (
    inspect_archive_aggtrades,
    iter_archive_aggtrades,
    normalize_archive_timestamp,
    parse_archive_aggtrade_row,
    read_checksum_file,
    verify_archive_checksum,
)


def test_normalize_archive_timestamp_handles_spot_microseconds() -> None:
    raw = 1_735_689_600_010_866
    assert normalize_archive_timestamp(raw, unit="auto") == 1_735_689_600_010
    assert normalize_archive_timestamp(raw, unit="microseconds") == 1_735_689_600_010


def test_normalize_archive_timestamp_preserves_milliseconds() -> None:
    raw = 1_735_689_600_010
    assert normalize_archive_timestamp(raw, unit="auto") == raw
    assert normalize_archive_timestamp(raw, unit="milliseconds") == raw


def test_parse_spot_archive_row_applies_explicit_availability_lag() -> None:
    row = [
        "42",
        "600.25000000",
        "1.50000000",
        "100",
        "101",
        "1735689600010866",
        "False",
        "True",
    ]
    trade = parse_archive_aggtrade_row(
        row,
        symbol="BNBUSDT",
        venue="spot",
        timestamp_unit="auto",
        availability_lag_ms=75,
    )
    assert trade.symbol == "BNBUSDT"
    assert trade.trade_timestamp_ms == 1_735_689_600_010
    assert trade.event_timestamp_ms == 1_735_689_600_085
    assert trade.price_e8 == 60_025_000_000
    assert trade.quantity_e8 == 150_000_000
    assert trade.aggressive_side == "buy"
    assert trade.aggregate_trade_id == 42


def test_parse_futures_archive_row_maps_buyer_maker_to_sell() -> None:
    row = [
        "26129",
        "600.25000000",
        "2.00000000",
        "27781",
        "27781",
        "1735689600010",
        "true",
    ]
    trade = parse_archive_aggtrade_row(
        row,
        symbol="BNBUSDT",
        venue="um_futures",
        timestamp_unit="milliseconds",
        availability_lag_ms=10,
    )
    assert trade.aggressive_side == "sell"
    assert trade.event_timestamp_ms == 1_735_689_600_020


def test_archive_iterator_rejects_non_increasing_ids(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        "1,600,1,10,10,1735689600000,false,true\n"
        "1,601,1,11,11,1735689600001,false,true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        tuple(
            iter_archive_aggtrades(
                path,
                symbol="BNBUSDT",
                venue="spot",
                timestamp_unit="milliseconds",
                availability_lag_ms=0,
            )
        )


def test_official_checksum_verification_accepts_matching_archive(tmp_path: Path) -> None:
    archive = tmp_path / "BNBUSDT-aggTrades-2026-08.zip"
    archive.write_bytes(b"deterministic-archive-bytes")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = tmp_path / f"{archive.name}.CHECKSUM"
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")

    assert read_checksum_file(checksum, expected_filename=archive.name) == digest
    assert verify_archive_checksum(archive, checksum) == digest


def test_official_checksum_verification_rejects_corruption(tmp_path: Path) -> None:
    archive = tmp_path / "BNBUSDT-aggTrades-2026-08.zip"
    archive.write_bytes(b"corrupted")
    checksum = tmp_path / f"{archive.name}.CHECKSUM"
    checksum.write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_archive_checksum(archive, checksum)


def test_checksum_rejects_wrong_archive_filename(tmp_path: Path) -> None:
    checksum = tmp_path / "data.CHECKSUM"
    checksum.write_text(f"{'a' * 64}  other.zip\n", encoding="utf-8")

    with pytest.raises(ValueError, match="filename mismatch"):
        read_checksum_file(checksum, expected_filename="expected.zip")


def test_inspect_zip_emits_source_provenance(tmp_path: Path) -> None:
    path = tmp_path / "BNBUSDT-aggTrades-2026-08.zip"
    csv_payload = (
        b"1,600,1,10,10,1786000000000000,false,true\n"
        b"2,601,2,11,12,1786000000001000,true,true\n"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("BNBUSDT-aggTrades-2026-08.csv", csv_payload)

    report = inspect_archive_aggtrades(
        path,
        symbol="BNBUSDT",
        venue="spot",
        timestamp_unit="auto",
        availability_lag_ms=25,
    )
    assert report.schema_version == 1
    assert report.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report.row_count == 2
    assert report.first_trade_timestamp_ms == 1_786_000_000_000
    assert report.last_trade_timestamp_ms == 1_786_000_000_001
    assert report.first_aggregate_trade_id == 1
    assert report.last_aggregate_trade_id == 2
    assert report.availability_lag_ms == 25
