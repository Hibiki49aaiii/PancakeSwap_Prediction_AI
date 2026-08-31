from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .clickhouse import ClickHouseJsonSource

_REQUIRED_BINANCE_COLUMNS: dict[str, str] = {
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
_REQUIRED_SORTING_KEY = (
    "venue,symbol,timestamp_unit,availability_lag_ms,aggregate_trade_id"
)


def _normalize_expression(value: str) -> str:
    normalized = re.sub(r"[\s`]", "", value)
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1]
    return normalized


def _engine_version_ready(engine_full: str | None) -> bool:
    if engine_full is None:
        return False
    normalized = _normalize_expression(engine_full)
    return normalized.startswith("ReplacingMergeTree(ingest_version)")


def _sorting_key_ready(sorting_key: str | None) -> bool:
    if sorting_key is None:
        return False
    normalized = _normalize_expression(sorting_key)
    if normalized.startswith("tuple(") and normalized.endswith(")"):
        normalized = normalized[6:-1]
    return normalized == _REQUIRED_SORTING_KEY


@dataclass(frozen=True, slots=True)
class ClickHouseBinanceSchemaReport:
    table_exists: bool
    engine: str | None
    engine_full: str | None
    sorting_key: str | None
    missing_columns: tuple[str, ...]
    incompatible_columns: tuple[str, ...]

    @property
    def engine_version_ready(self) -> bool:
        return _engine_version_ready(self.engine_full)

    @property
    def sorting_key_ready(self) -> bool:
        return _sorting_key_ready(self.sorting_key)

    @property
    def ready(self) -> bool:
        return (
            self.table_exists
            and self.engine == "ReplacingMergeTree"
            and self.engine_version_ready
            and self.sorting_key_ready
            and not self.missing_columns
            and not self.incompatible_columns
        )

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["engine_version_ready"] = self.engine_version_ready
        payload["sorting_key_ready"] = self.sorting_key_ready
        payload["ready"] = self.ready
        return payload


def inspect_binance_trade_schema(
    source: ClickHouseJsonSource,
) -> ClickHouseBinanceSchemaReport:
    table_rows = tuple(
        source.query_json_rows(
            "SELECT engine,engine_full,sorting_key FROM system.tables "
            "WHERE database=currentDatabase() AND name='binance_agg_trades'"
        )
    )
    if len(table_rows) > 1:
        raise ValueError("ClickHouse returned duplicate binance_agg_trades table metadata")
    row = None if not table_rows else table_rows[0]
    engine = None if row is None else str(row.get("engine"))
    engine_full = None if row is None else str(row.get("engine_full"))
    sorting_key = None if row is None else str(row.get("sorting_key"))

    column_rows = tuple(
        source.query_json_rows(
            "SELECT name,type FROM system.columns "
            "WHERE database=currentDatabase() AND table='binance_agg_trades' ORDER BY position"
        )
    )
    observed = {
        str(column.get("name")): str(column.get("type"))
        for column in column_rows
        if column.get("name") is not None and column.get("type") is not None
    }
    missing = tuple(sorted(set(_REQUIRED_BINANCE_COLUMNS) - set(observed)))
    incompatible = tuple(
        sorted(
            name
            for name, expected in _REQUIRED_BINANCE_COLUMNS.items()
            if name in observed and observed[name] != expected
        )
    )
    return ClickHouseBinanceSchemaReport(
        table_exists=bool(table_rows),
        engine=engine,
        engine_full=engine_full,
        sorting_key=sorting_key,
        missing_columns=missing,
        incompatible_columns=incompatible,
    )
