from __future__ import annotations

from dataclasses import asdict, dataclass

from .clickhouse import ClickHouseJsonSource

_REQUIRED_BINANCE_COLUMNS: dict[str, str] = {
    "venue": "LowCardinality(String)",
    "symbol": "LowCardinality(String)",
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


@dataclass(frozen=True, slots=True)
class ClickHouseBinanceSchemaReport:
    table_exists: bool
    engine: str | None
    missing_columns: tuple[str, ...]
    incompatible_columns: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return (
            self.table_exists
            and self.engine == "ReplacingMergeTree"
            and not self.missing_columns
            and not self.incompatible_columns
        )

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["ready"] = self.ready
        return payload


def inspect_binance_trade_schema(
    source: ClickHouseJsonSource,
) -> ClickHouseBinanceSchemaReport:
    table_rows = tuple(
        source.query_json_rows(
            "SELECT engine FROM system.tables "
            "WHERE database=currentDatabase() AND name='binance_agg_trades'"
        )
    )
    if len(table_rows) > 1:
        raise ValueError("ClickHouse returned duplicate binance_agg_trades table metadata")
    engine = None if not table_rows else str(table_rows[0].get("engine"))

    column_rows = tuple(
        source.query_json_rows(
            "SELECT name,type FROM system.columns "
            "WHERE database=currentDatabase() AND table='binance_agg_trades' ORDER BY position"
        )
    )
    observed = {
        str(row.get("name")): str(row.get("type"))
        for row in column_rows
        if row.get("name") is not None and row.get("type") is not None
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
        missing_columns=missing,
        incompatible_columns=incompatible,
    )
