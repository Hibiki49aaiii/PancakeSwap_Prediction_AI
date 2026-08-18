from __future__ import annotations

import csv
import gzip
import hashlib
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

LEGACY_ROUNDS_SOURCE_REPOSITORY = "xcnecon/PancakeSwap-Arbitrage-Bot"
LEGACY_ROUNDS_SOURCE_COMMIT = "93c7ec384ad9bedcf7824e8db050049ef891e367"
LEGACY_ROUNDS_SOURCE_BLOB_SHA = "6eeeb9a0b0a19e4b3bf9ca41563e2e9f05f051ce"
LEGACY_ROUNDS_SOURCE_PATH = "data/rounds.csv.gz"
LEGACY_ROUNDS_SOURCE_CLASS = "third_party_historical_benchmark"
LEGACY_ROUNDS_EXPECTED_FIRST_EPOCH = 100
LEGACY_ROUNDS_EXPECTED_LAST_EPOCH = 144_099
LEGACY_ROUNDS_EXPECTED_ROWS = 144_000

_REQUIRED_COLUMNS = (
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


class LegacyRoundDatasetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LegacyRoundRecord:
    epoch: int
    start_timestamp: int
    lock_timestamp: int
    close_timestamp: int
    lock_price_e8: int
    close_price_e8: int
    lock_oracle_id: int
    close_oracle_id: int
    total_amount_wei: int
    bull_amount_wei: int
    bear_amount_wei: int
    reward_base_cal_amount_wei: int
    reward_amount_wei: int
    oracle_called: bool

    @property
    def label(self) -> str:
        if not self.oracle_called:
            return "refunded"
        if self.close_price_e8 > self.lock_price_e8:
            return "bull"
        if self.close_price_e8 < self.lock_price_e8:
            return "bear"
        return "tie"


@dataclass(frozen=True, slots=True)
class LegacyRoundAuditReport:
    source_class: str
    authoritative: bool
    source_repository: str
    source_commit: str
    source_blob_sha: str
    source_path: str
    source_sha256: str
    row_count: int
    first_epoch: int | None
    last_epoch: int | None
    first_start_timestamp: int | None
    last_close_timestamp: int | None
    duplicate_epochs: int
    epoch_gaps: int
    non_monotonic_timestamps: int
    pool_sum_mismatches: int
    reward_base_mismatches: int
    reward_amount_exceeds_pool: int
    refunded_rounds: int
    tie_rounds: int
    empty_bull_pool_rounds: int
    empty_bear_pool_rounds: int
    expected_epoch_envelope_ready: bool
    structurally_ready: bool
    amount_precision_note: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _required_value(row: dict[str, str | None], field: str) -> str:
    value = row.get(field)
    if value is None or not value.strip():
        raise LegacyRoundDatasetError(f"legacy round row is missing {field}")
    return value.strip()


def _parse_int(row: dict[str, str | None], field: str) -> int:
    raw = _required_value(row, field)
    try:
        value = int(raw)
    except ValueError as exc:
        raise LegacyRoundDatasetError(f"legacy round {field} is not an integer") from exc
    if value < 0:
        raise LegacyRoundDatasetError(f"legacy round {field} must be non-negative")
    return value


def _parse_approx_wei(row: dict[str, str | None], field: str) -> int:
    raw = _required_value(row, field)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise LegacyRoundDatasetError(f"legacy round {field} is not decimal") from exc
    if not value.is_finite() or value < 0:
        raise LegacyRoundDatasetError(f"legacy round {field} must be finite and non-negative")
    return int(value.to_integral_value(rounding=ROUND_HALF_EVEN))


def _parse_bool(row: dict[str, str | None], field: str) -> bool:
    raw = _required_value(row, field).lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise LegacyRoundDatasetError(f"legacy round {field} must be true/false")


def parse_legacy_round_row(row: dict[str, str | None]) -> LegacyRoundRecord:
    return LegacyRoundRecord(
        epoch=_parse_int(row, "epoch"),
        start_timestamp=_parse_int(row, "startTimestamp"),
        lock_timestamp=_parse_int(row, "lockTimestamp"),
        close_timestamp=_parse_int(row, "closeTimestamp"),
        lock_price_e8=_parse_int(row, "lockPrice"),
        close_price_e8=_parse_int(row, "closePrice"),
        lock_oracle_id=_parse_int(row, "lockOracleId"),
        close_oracle_id=_parse_int(row, "closeOracleId"),
        total_amount_wei=_parse_approx_wei(row, "totalAmount"),
        bull_amount_wei=_parse_approx_wei(row, "bullAmount"),
        bear_amount_wei=_parse_approx_wei(row, "bearAmount"),
        reward_base_cal_amount_wei=_parse_approx_wei(row, "rewardBaseCalAmount"),
        reward_amount_wei=_parse_approx_wei(row, "rewardAmount"),
        oracle_called=_parse_bool(row, "oracleCalled"),
    )


def _validate_header(fieldnames: list[str] | None) -> None:
    if fieldnames is None:
        raise LegacyRoundDatasetError("legacy round CSV has no header")
    missing = sorted(set(_REQUIRED_COLUMNS) - set(fieldnames))
    if missing:
        raise LegacyRoundDatasetError(
            "legacy round CSV missing columns: " + ", ".join(missing)
        )


def iter_legacy_rounds(path: Path) -> Iterator[LegacyRoundRecord]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        _validate_header(reader.fieldnames)
        for raw_row in reader:
            yield parse_legacy_round_row(raw_row)


def load_legacy_rounds(path: Path) -> tuple[LegacyRoundRecord, ...]:
    return tuple(iter_legacy_rounds(path))


def audit_legacy_rounds(path: Path) -> LegacyRoundAuditReport:
    source_sha256 = _sha256_file(path)
    seen_epochs: set[int] = set()
    row_count = 0
    first_epoch: int | None = None
    last_epoch: int | None = None
    first_start: int | None = None
    last_close: int | None = None
    duplicate_epochs = 0
    epoch_gaps = 0
    non_monotonic_timestamps = 0
    pool_sum_mismatches = 0
    reward_base_mismatches = 0
    reward_amount_exceeds_pool = 0
    refunded_rounds = 0
    tie_rounds = 0
    empty_bull = 0
    empty_bear = 0
    previous_epoch: int | None = None

    for row in iter_legacy_rounds(path):
        row_count += 1
        if first_epoch is None:
            first_epoch = row.epoch
            first_start = row.start_timestamp
        last_epoch = row.epoch
        last_close = row.close_timestamp

        if row.epoch in seen_epochs:
            duplicate_epochs += 1
        seen_epochs.add(row.epoch)
        if previous_epoch is not None and row.epoch > previous_epoch + 1:
            epoch_gaps += row.epoch - previous_epoch - 1
        if previous_epoch is not None and row.epoch <= previous_epoch:
            non_monotonic_timestamps += 1
        previous_epoch = row.epoch

        if not row.start_timestamp <= row.lock_timestamp <= row.close_timestamp:
            non_monotonic_timestamps += 1
        if row.total_amount_wei != row.bull_amount_wei + row.bear_amount_wei:
            pool_sum_mismatches += 1
        if row.reward_amount_wei > row.total_amount_wei:
            reward_amount_exceeds_pool += 1

        if not row.oracle_called:
            refunded_rounds += 1
        elif row.label == "tie":
            tie_rounds += 1
        else:
            expected_base = (
                row.bull_amount_wei if row.label == "bull" else row.bear_amount_wei
            )
            if row.reward_base_cal_amount_wei != expected_base:
                reward_base_mismatches += 1

        if row.bull_amount_wei == 0:
            empty_bull += 1
        if row.bear_amount_wei == 0:
            empty_bear += 1

    envelope_ready = (
        row_count == LEGACY_ROUNDS_EXPECTED_ROWS
        and first_epoch == LEGACY_ROUNDS_EXPECTED_FIRST_EPOCH
        and last_epoch == LEGACY_ROUNDS_EXPECTED_LAST_EPOCH
    )
    structurally_ready = (
        row_count > 0
        and duplicate_epochs == 0
        and epoch_gaps == 0
        and non_monotonic_timestamps == 0
        and reward_amount_exceeds_pool == 0
    )
    return LegacyRoundAuditReport(
        source_class=LEGACY_ROUNDS_SOURCE_CLASS,
        authoritative=False,
        source_repository=LEGACY_ROUNDS_SOURCE_REPOSITORY,
        source_commit=LEGACY_ROUNDS_SOURCE_COMMIT,
        source_blob_sha=LEGACY_ROUNDS_SOURCE_BLOB_SHA,
        source_path=LEGACY_ROUNDS_SOURCE_PATH,
        source_sha256=source_sha256,
        row_count=row_count,
        first_epoch=first_epoch,
        last_epoch=last_epoch,
        first_start_timestamp=first_start,
        last_close_timestamp=last_close,
        duplicate_epochs=duplicate_epochs,
        epoch_gaps=epoch_gaps,
        non_monotonic_timestamps=non_monotonic_timestamps,
        pool_sum_mismatches=pool_sum_mismatches,
        reward_base_mismatches=reward_base_mismatches,
        reward_amount_exceeds_pool=reward_amount_exceeds_pool,
        refunded_rounds=refunded_rounds,
        tie_rounds=tie_rounds,
        empty_bull_pool_rounds=empty_bull,
        empty_bear_pool_rounds=empty_bear,
        expected_epoch_envelope_ready=envelope_ready,
        structurally_ready=structurally_ready,
        amount_precision_note=(
            "source amount columns use approximately six significant digits; "
            "integer wei values are rounded half-even for benchmark use only"
        ),
    )
