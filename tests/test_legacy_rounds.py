from __future__ import annotations

import csv
import gzip
from pathlib import Path

import pytest

from pancake_prediction.legacy_rounds import (
    LegacyRoundDatasetError,
    audit_legacy_rounds,
    parse_legacy_round_row,
)

_FIELDS = (
    "epoch",
    "startTimestamp",
    "lockTimestamp",
    "closeTimestamp",
    "lockPrice",
    "closePrice",
    "lockOracleId",
    "closeOracleId",
    "totalAmount",
    "bullAmount",
    "bearAmount",
    "rewardBaseCalAmount",
    "rewardAmount",
    "oracleCalled",
)


def _row(epoch: int, *, oracle_called: str = "True") -> dict[str, str]:
    return {
        "epoch": str(epoch),
        "startTimestamp": str(1_700_000_000 + epoch * 300),
        "lockTimestamp": str(1_700_000_300 + epoch * 300),
        "closeTimestamp": str(1_700_000_600 + epoch * 300),
        "lockPrice": "30000000000",
        "closePrice": "30100000000",
        "lockOracleId": str(10_000 + epoch),
        "closeOracleId": str(10_001 + epoch),
        "totalAmount": "1.00000E+18",
        "bullAmount": "6.00000E+17",
        "bearAmount": "4.00000E+17",
        "rewardBaseCalAmount": "6.00000E+17",
        "rewardAmount": "9.70000E+17",
        "oracleCalled": oracle_called,
    }


def _write_gzip(path: Path, rows: list[dict[str, str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_parse_legacy_round_row_supports_scientific_amounts() -> None:
    parsed = parse_legacy_round_row(_row(100))
    assert parsed.epoch == 100
    assert parsed.total_amount_wei == 10**18
    assert parsed.bull_amount_wei == 6 * 10**17
    assert parsed.bear_amount_wei == 4 * 10**17
    assert parsed.oracle_called is True
    assert parsed.label == "bull"


def test_audit_legacy_rounds_tracks_gaps_refunds_and_integrity(tmp_path: Path) -> None:
    path = tmp_path / "rounds.csv.gz"
    refunded = _row(102, oracle_called="False")
    refunded["rewardAmount"] = "0"
    refunded["rewardBaseCalAmount"] = "0"
    _write_gzip(path, [_row(100), refunded])

    report = audit_legacy_rounds(path)

    assert report.authoritative is False
    assert report.source_class == "third_party_historical_benchmark"
    assert report.row_count == 2
    assert report.first_epoch == 100
    assert report.last_epoch == 102
    assert report.epoch_gaps == 1
    assert report.refunded_rounds == 1
    assert report.pool_sum_mismatches == 0
    assert report.reward_base_mismatches == 0
    assert report.expected_epoch_envelope_ready is False
    assert report.structurally_ready is False


def test_audit_legacy_rounds_allows_approximate_pool_rounding_without_hiding_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rounds.csv.gz"
    row = _row(100)
    row["totalAmount"] = "1.00000E+18"
    row["bullAmount"] = "6.00001E+17"
    row["bearAmount"] = "4.00000E+17"
    _write_gzip(path, [row])

    report = audit_legacy_rounds(path)

    assert report.pool_sum_mismatches == 1
    assert "six significant digits" in report.amount_precision_note


def test_legacy_round_parser_rejects_bad_boolean_and_missing_column(tmp_path: Path) -> None:
    bad = _row(100)
    bad["oracleCalled"] = "yes"
    with pytest.raises(LegacyRoundDatasetError, match="true/false"):
        parse_legacy_round_row(bad)

    path = tmp_path / "bad.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS[:-1])
        writer.writeheader()
        partial = _row(100)
        partial.pop("oracleCalled")
        writer.writerow(partial)
    with pytest.raises(LegacyRoundDatasetError, match="missing columns"):
        audit_legacy_rounds(path)
