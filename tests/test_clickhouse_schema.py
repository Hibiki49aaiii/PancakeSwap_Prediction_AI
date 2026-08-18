from __future__ import annotations

from collections.abc import Iterator

from pancake_prediction.clickhouse_schema import inspect_binance_trade_schema


class MetadataSource:
    def __init__(
        self,
        *,
        engine: str | None,
        columns: dict[str, str],
    ) -> None:
        self.engine = engine
        self.columns = columns

    def query_json_rows(self, query: str) -> Iterator[dict[str, object]]:
        if "system.tables" in query:
            if self.engine is not None:
                yield {"engine": self.engine}
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


def test_schema_gate_accepts_retry_safe_binance_table() -> None:
    report = inspect_binance_trade_schema(
        MetadataSource(engine="ReplacingMergeTree", columns=_ready_columns())
    )
    assert report.ready is True
    assert report.missing_columns == ()
    assert report.incompatible_columns == ()


def test_schema_gate_rejects_old_merge_tree_schema() -> None:
    columns = _ready_columns()
    columns.pop("source_sha256")
    columns.pop("ingest_version")
    report = inspect_binance_trade_schema(
        MetadataSource(engine="MergeTree", columns=columns)
    )
    assert report.ready is False
    assert report.engine == "MergeTree"
    assert report.missing_columns == ("ingest_version", "source_sha256")


def test_schema_gate_rejects_incompatible_column_type() -> None:
    columns = _ready_columns()
    columns["availability_lag_ms"] = "UInt64"
    report = inspect_binance_trade_schema(
        MetadataSource(engine="ReplacingMergeTree", columns=columns)
    )
    assert report.ready is False
    assert report.incompatible_columns == ("availability_lag_ms",)
