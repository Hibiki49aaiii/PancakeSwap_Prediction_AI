from __future__ import annotations

from collections.abc import Iterator

from pancake_prediction.clickhouse_schema import inspect_binance_trade_schema


class MetadataSource:
    def __init__(
        self,
        *,
        engine: str | None,
        engine_full: str | None = None,
        sorting_key: str | None = None,
        columns: dict[str, str],
    ) -> None:
        self.engine = engine
        self.engine_full = engine_full
        self.sorting_key = sorting_key
        self.columns = columns

    def query_json_rows(self, query: str) -> Iterator[dict[str, object]]:
        if "system.tables" in query:
            if self.engine is not None:
                yield {
                    "engine": self.engine,
                    "engine_full": self.engine_full,
                    "sorting_key": self.sorting_key,
                }
            return
        if "system.columns" in query:
            for name, value_type in self.columns.items():
                yield {"name": name, "type": value_type}
            return
        raise AssertionError(f"unexpected query: {query}")


def _ready_columns() -> dict[str, str]:
    return {
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


def _ready_source(**overrides: object) -> MetadataSource:
    values: dict[str, object] = {
        "engine": "ReplacingMergeTree",
        "engine_full": "ReplacingMergeTree(ingest_version)",
        "sorting_key": "venue, symbol, availability_lag_ms, aggregate_trade_id",
        "columns": _ready_columns(),
    }
    values.update(overrides)
    return MetadataSource(
        engine=values["engine"] if isinstance(values["engine"], str) else None,
        engine_full=(
            values["engine_full"] if isinstance(values["engine_full"], str) else None
        ),
        sorting_key=(
            values["sorting_key"] if isinstance(values["sorting_key"], str) else None
        ),
        columns=values["columns"] if isinstance(values["columns"], dict) else {},
    )


def test_schema_gate_accepts_retry_safe_binance_table() -> None:
    report = inspect_binance_trade_schema(_ready_source())
    assert report.ready is True
    assert report.engine_version_ready is True
    assert report.sorting_key_ready is True
    assert report.missing_columns == ()
    assert report.incompatible_columns == ()


def test_schema_gate_accepts_parenthesized_backticked_sorting_key() -> None:
    report = inspect_binance_trade_schema(
        _ready_source(
            sorting_key="(`venue`, `symbol`, `availability_lag_ms`, `aggregate_trade_id`)"
        )
    )
    assert report.ready is True


def test_schema_gate_rejects_old_merge_tree_schema() -> None:
    columns = _ready_columns()
    columns.pop("source_sha256")
    columns.pop("ingest_version")
    report = inspect_binance_trade_schema(
        _ready_source(
            engine="MergeTree",
            engine_full="MergeTree",
            columns=columns,
        )
    )
    assert report.ready is False
    assert report.engine == "MergeTree"
    assert report.engine_version_ready is False
    assert report.missing_columns == ("ingest_version", "source_sha256")


def test_schema_gate_rejects_replacing_merge_tree_without_version_column() -> None:
    report = inspect_binance_trade_schema(
        _ready_source(engine_full="ReplacingMergeTree")
    )
    assert report.ready is False
    assert report.engine_version_ready is False


def test_schema_gate_rejects_sorting_key_without_latency_dimension() -> None:
    report = inspect_binance_trade_schema(
        _ready_source(sorting_key="venue, symbol, aggregate_trade_id")
    )
    assert report.ready is False
    assert report.sorting_key_ready is False


def test_schema_gate_rejects_incompatible_column_type() -> None:
    columns = _ready_columns()
    columns["availability_lag_ms"] = "UInt64"
    report = inspect_binance_trade_schema(_ready_source(columns=columns))
    assert report.ready is False
    assert report.incompatible_columns == ("availability_lag_ms",)
