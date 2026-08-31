from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from pancake_prediction import cli
from pancake_prediction.store import EventStore


def test_cli_oracle_history_report_uses_active_anchor(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "history.sqlite3"
    store = EventStore(database)
    store.initialize()
    store.record_metadata("BNBUSD.oracle_anchor_block", "100")
    store.record_metadata("BNBUSD.oracle_anchor_address", "0x" + "11" * 20)

    assert (
        cli.main(
            [
                "oracle-history-report",
                "--market",
                "BNBUSD",
                "--db",
                str(database),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["anchor"]["block_number"] == 100
    assert payload["active_answer_updates"] == 0


def test_cli_binance_archive_inspect_verifies_checksum_and_provenance(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive_path = tmp_path / "BNBUSDT-aggTrades-2026-08.zip"
    csv_payload = b"1,600,1,10,10,1786000000000000,false,true\n"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("BNBUSDT-aggTrades-2026-08.csv", csv_payload)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = tmp_path / f"{archive_path.name}.CHECKSUM"
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")

    assert (
        cli.main(
            [
                "binance-archive-inspect",
                "--market",
                "BNBUSD",
                "--archive",
                str(archive_path),
                "--checksum",
                str(checksum_path),
                "--venue",
                "spot",
                "--timestamp-unit",
                "auto",
                "--availability-lag-ms",
                "25",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["checksum_verified"] is True
    assert payload["source_sha256"] == digest
    assert payload["symbol"] == "BNBUSDT"
    assert payload["row_count"] == 1
    assert payload["availability_lag_ms"] == 25
