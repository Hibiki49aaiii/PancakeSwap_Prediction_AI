from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field

import pytest

from pancake_prediction.binance_live import (
    BinanceLiveError,
    BinanceRestPage,
    inspect_binance_live_coverage,
    latest_binance_live_cursor,
    sync_binance_live_aggtrades,
)
from pancake_prediction.clickhouse import ClickHouseInsertReport, QueryParameter


@dataclass
class FakeClickHouse:
    cursor_rows: tuple[dict[str, object], ...] = ()
    coverage_rows: tuple[dict[str, object], ...] = (
        {
            "row_count": 0,
            "first_available_at_ms": 0,
            "last_available_at_ms": 0,
        },
    )
    inserted: list[dict[str, object]] = field(default_factory=list)
    queries: list[tuple[str, Mapping[str, QueryParameter] | None]] = field(
        default_factory=list
    )

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        self.queries.append((query, parameters))
        if "count() AS row_count" in query:
            yield from self.coverage_rows
        else:
            yield from self.cursor_rows

    def insert_json_rows(
        self,
        table: str,
        rows: Iterable[Mapping[str, object]],
        *,
        batch_size: int,
    ) -> ClickHouseInsertReport:
        assert table == "binance_agg_trades"
        assert batch_size > 0
        materialized = [dict(row) for row in rows]
        self.inserted.extend(materialized)
        return ClickHouseInsertReport(
            batches=1 if materialized else 0,
            rows=len(materialized),
        )


@dataclass
class FakeRest:
    pages: list[BinanceRestPage]
    calls: list[dict[str, int | str]] = field(default_factory=list)

    def fetch_agg_trades(
        self,
        *,
        venue: str,
        symbol: str,
        parameters: Mapping[str, int | str],
    ) -> BinanceRestPage:
        assert venue in {"spot", "um_futures"}
        assert symbol == "BNBUSDT"
        self.calls.append(dict(parameters))
        if not self.pages:
            raise AssertionError("unexpected extra REST page request")
        return self.pages.pop(0)


def _row(
    trade_id: int,
    timestamp_ms: int,
    *,
    maker: bool = False,
) -> dict[str, object]:
    return {
        "a": trade_id,
        "p": "700.12345678",
        "q": "1.25000000",
        "f": trade_id,
        "l": trade_id,
        "T": timestamp_ms,
        "m": maker,
    }


def _page(
    rows: tuple[dict[str, object], ...],
    *,
    observed_at_ms: int,
    digest: str = "a" * 64,
) -> BinanceRestPage:
    return BinanceRestPage(
        rows=rows,
        observed_at_ms=observed_at_ms,
        source_sha256=digest,
    )


def test_live_sync_bootstraps_and_uses_actual_observation_time() -> None:
    clickhouse = FakeClickHouse()
    rest = FakeRest(
        [
            _page(
                (
                    _row(10, 1_000_010),
                    _row(11, 1_000_020, maker=True),
                ),
                observed_at_ms=1_000_100,
            )
        ]
    )

    report = sync_binance_live_aggtrades(
        clickhouse,
        clickhouse,
        rest,
        market="BNBUSD",
        venue="spot",
        availability_lag_ms=25,
        now_timestamp_ms=1_000_200,
        bootstrap_window_ms=1_000,
        ingest_version=123,
    )

    assert report.rows == 2
    assert report.pages == 1
    assert report.bootstrap_start_timestamp_ms == 999_200
    assert report.requested_end_timestamp_ms == 1_000_200
    assert report.resumed_from_aggregate_trade_id is None
    assert report.first_aggregate_trade_id == 10
    assert report.last_aggregate_trade_id == 11
    assert rest.calls == [
        {
            "startTime": 999_200,
            "endTime": 1_000_200,
            "limit": 1_000,
        }
    ]
    assert [row["event_timestamp_ms"] for row in clickhouse.inserted] == [
        1_000_100,
        1_000_100,
    ]
    assert clickhouse.inserted[0]["aggressive_side"] == "buy"
    assert clickhouse.inserted[1]["aggressive_side"] == "sell"
    assert clickhouse.inserted[0]["source_name"] == "binance-rest:spot"
    assert clickhouse.inserted[0]["timestamp_unit"] == "milliseconds"
    assert report.timestamp_unit == "milliseconds"
    assert clickhouse.inserted[0]["ingest_version"] == 123


def test_live_sync_resumes_from_latest_clickhouse_trade_id() -> None:
    clickhouse = FakeClickHouse(
        cursor_rows=(
            {
                "aggregate_trade_id": 500,
                "trade_timestamp_ms": 1_000_000,
                "source_name": "binance-rest:um_futures",
            },
        )
    )
    rest = FakeRest(
        [
            _page(
                (
                    _row(501, 1_000_010),
                    _row(502, 1_000_020),
                ),
                observed_at_ms=1_000_100,
            )
        ]
    )

    report = sync_binance_live_aggtrades(
        clickhouse,
        clickhouse,
        rest,
        market="BNBUSD",
        venue="um_futures",
        availability_lag_ms=10,
        now_timestamp_ms=1_000_200,
        ingest_version=456,
    )

    assert report.resumed_from_aggregate_trade_id == 501
    assert report.bootstrap_start_timestamp_ms is None
    assert rest.calls == [{"fromId": 501, "limit": 1_000}]
    assert [row["aggregate_trade_id"] for row in clickhouse.inserted] == [501, 502]


def test_stale_futures_cursor_falls_back_to_recent_bootstrap_window() -> None:
    now_ms = 200_000_000
    clickhouse = FakeClickHouse(
        cursor_rows=(
            {
                "aggregate_trade_id": 500,
                "trade_timestamp_ms": now_ms - 49 * 60 * 60 * 1_000,
                "source_name": "binance-rest:um_futures",
            },
        )
    )
    rest = FakeRest([_page((), observed_at_ms=now_ms)])

    report = sync_binance_live_aggtrades(
        clickhouse,
        clickhouse,
        rest,
        market="BNBUSD",
        venue="um_futures",
        availability_lag_ms=0,
        now_timestamp_ms=now_ms,
        bootstrap_window_ms=60_000,
        ingest_version=789,
    )

    assert report.resumed_from_aggregate_trade_id is None
    assert report.bootstrap_start_timestamp_ms == now_ms - 60_000
    assert rest.calls[0] == {
        "startTime": now_ms - 60_000,
        "endTime": now_ms,
        "limit": 1_000,
    }


def test_live_sync_pages_by_trade_id_and_stops_at_requested_end() -> None:
    first_rows = tuple(_row(index, 1_000 + index) for index in range(1_000))
    second_rows = (
        _row(1_000, 2_000),
        _row(1_001, 2_100),
    )
    clickhouse = FakeClickHouse()
    rest = FakeRest(
        [
            _page(first_rows, observed_at_ms=3_000, digest="a" * 64),
            _page(second_rows, observed_at_ms=3_100, digest="b" * 64),
        ]
    )

    report = sync_binance_live_aggtrades(
        clickhouse,
        clickhouse,
        rest,
        market="BNBUSD",
        venue="spot",
        availability_lag_ms=0,
        now_timestamp_ms=2_050,
        bootstrap_window_ms=2_000,
        ingest_version=100,
    )

    assert report.pages == 2
    assert report.rows == 1_001
    assert rest.calls[1] == {"fromId": 1_000, "limit": 1_000}
    assert report.last_aggregate_trade_id == 1_000
    assert clickhouse.inserted[-1]["aggregate_trade_id"] == 1_000
    assert report.response_chain_sha256 != "0" * 64


def test_live_sync_fails_closed_when_max_pages_exhausted() -> None:
    full_page = tuple(_row(index, 1_000 + index) for index in range(1_000))
    clickhouse = FakeClickHouse()
    rest = FakeRest([_page(full_page, observed_at_ms=5_000)])

    with pytest.raises(BinanceLiveError, match="max_pages"):
        sync_binance_live_aggtrades(
            clickhouse,
            clickhouse,
            rest,
            market="BNBUSD",
            venue="spot",
            availability_lag_ms=0,
            now_timestamp_ms=10_000,
            bootstrap_window_ms=9_000,
            max_pages=1,
            ingest_version=100,
        )


def test_live_sync_rejects_non_boolean_maker_flag() -> None:
    clickhouse = FakeClickHouse()
    invalid = _row(1, 1_000)
    invalid["m"] = "false"
    rest = FakeRest([_page((invalid,), observed_at_ms=2_000)])

    with pytest.raises(ValueError, match="JSON boolean"):
        sync_binance_live_aggtrades(
            clickhouse,
            clickhouse,
            rest,
            market="BNBUSD",
            venue="spot",
            availability_lag_ms=0,
            now_timestamp_ms=2_000,
            ingest_version=100,
        )


def test_latest_live_cursor_is_parameterized() -> None:
    clickhouse = FakeClickHouse(
        cursor_rows=(
            {
                "aggregate_trade_id": 42,
                "trade_timestamp_ms": 1_234,
                "source_name": "binance-rest:spot",
            },
        )
    )
    cursor = latest_binance_live_cursor(
        clickhouse,
        market="BNBUSD",
        venue="spot",
        availability_lag_ms=25,
    )

    assert cursor is not None
    assert cursor.aggregate_trade_id == 42
    assert cursor.trade_timestamp_ms == 1_234
    assert cursor.source_name == "binance-rest:spot"
    query, parameters = clickhouse.queries[0]
    assert "FINAL" in query
    assert parameters == {
        "venue": "spot",
        "symbol": "BNBUSDT",
        "timestamp_unit": "milliseconds",
        "availability_lag_ms": 25,
    }

def test_live_coverage_is_source_bound_and_parameterized() -> None:
    clickhouse = FakeClickHouse(
        coverage_rows=(
            {
                "row_count": 3,
                "first_available_at_ms": 1_000,
                "last_available_at_ms": 2_000,
            },
        )
    )
    coverage = inspect_binance_live_coverage(
        clickhouse,
        market="BNBUSD",
        venue="spot",
        availability_lag_ms=25,
        timestamp_unit="auto",
    )

    assert coverage.row_count == 3
    assert coverage.first_available_at_ms == 1_000
    assert coverage.last_available_at_ms == 2_000
    query, parameters = clickhouse.queries[0]
    assert "source_name={source_name:String}" in query
    assert parameters == {
        "venue": "spot",
        "symbol": "BNBUSDT",
        "timestamp_unit": "auto",
        "availability_lag_ms": 25,
        "source_name": "binance-rest:spot",
    }


def test_live_coverage_empty_lineage_has_no_available_timestamp() -> None:
    clickhouse = FakeClickHouse(
        coverage_rows=(
            {
                "row_count": 0,
                "first_available_at_ms": 0,
                "last_available_at_ms": 0,
            },
        )
    )
    coverage = inspect_binance_live_coverage(
        clickhouse,
        market="BNBUSD",
        venue="um_futures",
        availability_lag_ms=250,
    )

    assert coverage.row_count == 0
    assert coverage.first_available_at_ms is None
    assert coverage.last_available_at_ms is None

def test_live_sync_rejects_non_live_cursor_after_prospective_start() -> None:
    clickhouse = FakeClickHouse(
        cursor_rows=(
            {
                "aggregate_trade_id": 500,
                "trade_timestamp_ms": 1_000_000,
                "source_name": "binance-archive:spot",
            },
        ),
        coverage_rows=(
            {
                "row_count": 10,
                "first_available_at_ms": 900_000,
                "last_available_at_ms": 999_000,
            },
        ),
    )
    rest = FakeRest([])

    with pytest.raises(BinanceLiveError, match="source-bound campaign"):
        sync_binance_live_aggtrades(
            clickhouse,
            clickhouse,
            rest,
            market="BNBUSD",
            venue="spot",
            availability_lag_ms=25,
            now_timestamp_ms=1_000_200,
            ingest_version=123,
        )

