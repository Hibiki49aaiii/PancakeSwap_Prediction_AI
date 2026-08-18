from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .binance import AggTrade
from .binance_archive import (
    ArchiveVenue,
    TimestampUnit,
    iter_archive_aggtrades,
    verify_archive_checksum,
)
from .research_dataset import BINANCE_SYMBOL_BY_MARKET

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ClickHouseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ClickHouseInsertReport:
    batches: int
    rows: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BinanceArchiveIngestReport:
    market: str
    venue: ArchiveVenue
    symbol: str
    source_name: str
    source_sha256: str
    timestamp_unit: TimestampUnit
    availability_lag_ms: int
    ingest_version: int
    batches: int
    rows: int
    first_aggregate_trade_id: int | None
    last_aggregate_trade_id: int | None
    first_trade_timestamp_ms: int | None
    last_trade_timestamp_ms: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ClickHouseJsonSink(Protocol):
    def insert_json_rows(
        self,
        table: str,
        rows: Iterable[Mapping[str, object]],
        *,
        batch_size: int,
    ) -> ClickHouseInsertReport: ...


class ClickHouseJsonSource(Protocol):
    def query_json_rows(self, query: str) -> Iterator[dict[str, object]]: ...


def _validated_identifier(value: str, *, field: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid ClickHouse {field}: {value!r}")
    return value


def _json_each_row_payload(rows: Iterable[Mapping[str, object]]) -> bytes:
    encoded = [
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        for row in rows
    ]
    if not encoded:
        return b""
    return ("\n".join(encoded) + "\n").encode()


def _batches(
    rows: Iterable[Mapping[str, object]], *, batch_size: int
) -> Iterator[tuple[Mapping[str, object], ...]]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    batch: list[Mapping[str, object]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            yield tuple(batch)
            batch.clear()
    if batch:
        yield tuple(batch)


@dataclass(slots=True)
class ClickHouseHttpClient:
    endpoint: str
    database: str = "default"
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ClickHouse endpoint must be an http(s) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("ClickHouse credentials must not be embedded in the endpoint URL")
        _validated_identifier(self.database, field="database")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/octet-stream",
            "X-ClickHouse-Database": self.database,
        }
        if self.username is not None:
            headers["X-ClickHouse-User"] = self.username
        if self.password is not None:
            headers["X-ClickHouse-Key"] = self.password
        return headers

    def _post(self, query: str, data: bytes = b"") -> bytes:
        payload = query.encode() + b"\n" + data
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read(1_000).decode(errors="replace").strip()
            detail = f": {body}" if body else ""
            raise ClickHouseError(f"ClickHouse HTTP {exc.code}{detail}") from exc
        except urllib.error.URLError as exc:
            raise ClickHouseError(f"ClickHouse request failed: {exc.reason}") from exc

    def execute(self, query: str) -> str:
        return self._post(query).decode(errors="replace")

    def insert_json_rows(
        self,
        table: str,
        rows: Iterable[Mapping[str, object]],
        *,
        batch_size: int,
    ) -> ClickHouseInsertReport:
        table_name = _validated_identifier(table, field="table")
        batches = 0
        row_count = 0
        for batch in _batches(rows, batch_size=batch_size):
            payload = _json_each_row_payload(batch)
            self._post(f"INSERT INTO {table_name} FORMAT JSONEachRow", payload)
            batches += 1
            row_count += len(batch)
        return ClickHouseInsertReport(batches=batches, rows=row_count)

    def query_json_rows(self, query: str) -> Iterator[dict[str, object]]:
        raw = self._post(query.rstrip() + " FORMAT JSONEachRow")
        for line in raw.splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ClickHouseError("ClickHouse JSONEachRow response contained a non-object row")
            yield {str(key): item for key, item in value.items()}


def _archive_rows(
    archive_path: Path,
    *,
    symbol: str,
    venue: ArchiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
    source_sha256: str,
    ingest_version: int,
    stats: dict[str, int | None],
) -> Iterator[Mapping[str, object]]:
    for trade in iter_archive_aggtrades(
        archive_path,
        symbol=symbol,
        venue=venue,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
    ):
        trade_id = trade.aggregate_trade_id
        trade_timestamp_ms = trade.trade_timestamp_ms
        if stats["first_id"] is None:
            stats["first_id"] = trade_id
            stats["first_timestamp_ms"] = trade_timestamp_ms
        stats["last_id"] = trade_id
        stats["last_timestamp_ms"] = trade_timestamp_ms
        yield {
            "venue": venue,
            "symbol": symbol,
            "event_timestamp_ms": trade.event_timestamp_ms,
            "trade_timestamp_ms": trade_timestamp_ms,
            "aggregate_trade_id": trade_id,
            "price_e8": trade.price_e8,
            "quantity_e8": trade.quantity_e8,
            "aggressive_side": trade.aggressive_side,
            "source_sha256": source_sha256,
            "source_name": archive_path.name,
            "availability_lag_ms": availability_lag_ms,
            "ingest_version": ingest_version,
        }


def ingest_binance_archive(
    sink: ClickHouseJsonSink,
    archive_path: Path,
    checksum_path: Path,
    *,
    market: str,
    venue: ArchiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
    batch_size: int = 50_000,
    ingest_version: int | None = None,
) -> BinanceArchiveIngestReport:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    version = time.time_ns() if ingest_version is None else ingest_version
    if version < 0 or version > (1 << 64) - 1:
        raise ValueError("ingest_version must fit UInt64")

    source_sha256 = verify_archive_checksum(archive_path, checksum_path)
    symbol = BINANCE_SYMBOL_BY_MARKET[market]
    stats: dict[str, int | None] = {
        "first_id": None,
        "last_id": None,
        "first_timestamp_ms": None,
        "last_timestamp_ms": None,
    }
    insert_report = sink.insert_json_rows(
        "binance_agg_trades",
        _archive_rows(
            archive_path,
            symbol=symbol,
            venue=venue,
            timestamp_unit=timestamp_unit,
            availability_lag_ms=availability_lag_ms,
            source_sha256=source_sha256,
            ingest_version=version,
            stats=stats,
        ),
        batch_size=batch_size,
    )
    return BinanceArchiveIngestReport(
        market=market,
        venue=venue,
        symbol=symbol,
        source_name=archive_path.name,
        source_sha256=source_sha256,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
        ingest_version=version,
        batches=insert_report.batches,
        rows=insert_report.rows,
        first_aggregate_trade_id=stats["first_id"],
        last_aggregate_trade_id=stats["last_id"],
        first_trade_timestamp_ms=stats["first_timestamp_ms"],
        last_trade_timestamp_ms=stats["last_timestamp_ms"],
    )


def _quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def load_binance_trade_window(
    source: ClickHouseJsonSource,
    *,
    market: str,
    venue: ArchiveVenue,
    availability_lag_ms: int,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> tuple[AggTrade, ...]:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    if start_timestamp_ms < 0 or end_timestamp_ms <= start_timestamp_ms:
        raise ValueError("invalid trade window")
    symbol = BINANCE_SYMBOL_BY_MARKET[market]
    query = (
        "SELECT symbol,event_timestamp_ms,trade_timestamp_ms,price_e8,quantity_e8,"
        "aggressive_side,aggregate_trade_id FROM binance_agg_trades FINAL WHERE "
        f"venue={_quoted(venue)} AND symbol={_quoted(symbol)} AND "
        f"availability_lag_ms={availability_lag_ms} AND "
        f"trade_timestamp_ms>={start_timestamp_ms} AND "
        f"trade_timestamp_ms<{end_timestamp_ms} ORDER BY "
        "trade_timestamp_ms,aggregate_trade_id"
    )
    trades: list[AggTrade] = []
    for row in source.query_json_rows(query):
        trades.append(
            AggTrade(
                symbol=str(row["symbol"]),
                event_timestamp_ms=int(row["event_timestamp_ms"]),
                trade_timestamp_ms=int(row["trade_timestamp_ms"]),
                price_e8=int(row["price_e8"]),
                quantity_e8=int(row["quantity_e8"]),
                aggressive_side=str(row["aggressive_side"]),
                aggregate_trade_id=int(row["aggregate_trade_id"]),
            )
        )
    return tuple(trades)
