from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, TextIO

from .binance import PRICE_SCALE, QTY_SCALE, AggTrade, decimal_to_fixed

ArchiveVenue = Literal["spot", "um_futures", "cm_futures"]
TimestampUnit = Literal["auto", "milliseconds", "microseconds"]


@dataclass(frozen=True, slots=True)
class BinanceArchiveProvenance:
    schema_version: int
    source_sha256: str
    source_name: str
    venue: ArchiveVenue
    symbol: str
    timestamp_unit: TimestampUnit
    availability_lag_ms: int
    row_count: int
    first_trade_timestamp_ms: int | None
    last_trade_timestamp_ms: int | None
    first_aggregate_trade_id: int | None
    last_aggregate_trade_id: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_archive_timestamp(value: object, *, unit: TimestampUnit) -> int:
    raw = int(str(value))
    if raw < 0:
        raise ValueError("archive timestamp must be non-negative")
    resolved_unit = unit
    if unit == "auto":
        resolved_unit = "microseconds" if raw >= 100_000_000_000_000 else "milliseconds"
    if resolved_unit == "microseconds":
        return raw // 1_000
    if resolved_unit == "milliseconds":
        return raw
    raise ValueError(f"unsupported timestamp unit: {unit}")


def _parse_bool(value: object) -> bool:
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def parse_archive_aggtrade_row(
    row: Sequence[str],
    *,
    symbol: str,
    venue: ArchiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
) -> AggTrade:
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    minimum_columns = 8 if venue == "spot" else 7
    if len(row) < minimum_columns:
        raise ValueError(
            f"archive aggTrade row has {len(row)} columns; expected at least {minimum_columns}"
        )
    aggregate_trade_id = int(row[0])
    trade_timestamp_ms = normalize_archive_timestamp(row[5], unit=timestamp_unit)
    buyer_is_maker = _parse_bool(row[6])
    return AggTrade(
        symbol=symbol.upper(),
        event_timestamp_ms=trade_timestamp_ms + availability_lag_ms,
        trade_timestamp_ms=trade_timestamp_ms,
        price_e8=decimal_to_fixed(row[1], scale=PRICE_SCALE),
        quantity_e8=decimal_to_fixed(row[2], scale=QTY_SCALE),
        aggressive_side="sell" if buyer_is_maker else "buy",
        aggregate_trade_id=aggregate_trade_id,
    )


def _looks_like_header(row: Sequence[str]) -> bool:
    if not row:
        return False
    first = row[0].strip().lower().replace(" ", "")
    return first in {"aggtradeid", "aggregatetradeid", "aggregate_tradeid"}


def _iter_rows(handle: TextIO) -> Iterator[list[str]]:
    reader = csv.reader(handle)
    first = True
    for row in reader:
        if not row or all(not value.strip() for value in row):
            continue
        if first and _looks_like_header(row):
            first = False
            continue
        first = False
        yield row


def _archive_csv_handle(path: Path) -> tuple[TextIO, zipfile.ZipFile | None]:
    if path.suffix.lower() != ".zip":
        return path.open("r", encoding="utf-8", newline=""), None

    archive = zipfile.ZipFile(path)
    members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(members) != 1:
        archive.close()
        raise ValueError("Binance archive ZIP must contain exactly one CSV member")
    raw = archive.open(members[0], "r")
    return io.TextIOWrapper(raw, encoding="utf-8", newline=""), archive


def iter_archive_aggtrades(
    path: Path,
    *,
    symbol: str,
    venue: ArchiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
) -> Iterator[AggTrade]:
    handle, archive = _archive_csv_handle(path)
    try:
        previous_id: int | None = None
        previous_timestamp_ms: int | None = None
        for row in _iter_rows(handle):
            trade = parse_archive_aggtrade_row(
                row,
                symbol=symbol,
                venue=venue,
                timestamp_unit=timestamp_unit,
                availability_lag_ms=availability_lag_ms,
            )
            if previous_id is not None and trade.aggregate_trade_id <= previous_id:
                raise ValueError("aggregate trade ids must be strictly increasing")
            if (
                previous_timestamp_ms is not None
                and trade.trade_timestamp_ms < previous_timestamp_ms
            ):
                raise ValueError("archive trade timestamps must be non-decreasing")
            previous_id = trade.aggregate_trade_id
            previous_timestamp_ms = trade.trade_timestamp_ms
            yield trade
    finally:
        handle.close()
        if archive is not None:
            archive.close()


def inspect_archive_aggtrades(
    path: Path,
    *,
    symbol: str,
    venue: ArchiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
) -> BinanceArchiveProvenance:
    first_timestamp: int | None = None
    last_timestamp: int | None = None
    first_id: int | None = None
    last_id: int | None = None
    row_count = 0
    for trade in iter_archive_aggtrades(
        path,
        symbol=symbol,
        venue=venue,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
    ):
        if first_timestamp is None:
            first_timestamp = trade.trade_timestamp_ms
            first_id = trade.aggregate_trade_id
        last_timestamp = trade.trade_timestamp_ms
        last_id = trade.aggregate_trade_id
        row_count += 1
    return BinanceArchiveProvenance(
        schema_version=1,
        source_sha256=sha256_file(path),
        source_name=path.name,
        venue=venue,
        symbol=symbol.upper(),
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
        row_count=row_count,
        first_trade_timestamp_ms=first_timestamp,
        last_trade_timestamp_ms=last_timestamp,
        first_aggregate_trade_id=first_id,
        last_aggregate_trade_id=last_id,
    )


def materialize_archive_aggtrades(
    rows: Iterable[AggTrade],
) -> tuple[AggTrade, ...]:
    return tuple(rows)
