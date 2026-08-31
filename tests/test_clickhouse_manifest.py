from __future__ import annotations

from collections.abc import Iterator, Mapping

from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.clickhouse_manifest import (
    BinanceSourceSlice,
    ClickHouseResearchCampaignManifest,
    load_binance_source_slices,
)


class ProvenanceSource:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: Mapping[str, QueryParameter] | None = None

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        self.query = query
        self.parameters = parameters
        yield {
            "source_sha256": "a" * 64,
            "source_name": "BNBUSDT-aggTrades-2026-08-01.zip",
            "row_count": "3",
            "first_trade_timestamp_ms": "1785542400000",
            "last_trade_timestamp_ms": "1785542400002",
            "first_aggregate_trade_id": "1",
            "last_aggregate_trade_id": "3",
        }


def _slice(source_sha256: str = "a" * 64) -> BinanceSourceSlice:
    return BinanceSourceSlice(
        venue="spot",
        symbol="BNBUSDT",
        timestamp_unit="auto",
        availability_lag_ms=25,
        source_sha256=source_sha256,
        source_name="BNBUSDT-aggTrades-2026-08-01.zip",
        row_count=3,
        first_trade_timestamp_ms=1_785_542_400_000,
        last_trade_timestamp_ms=1_785_542_400_002,
        first_aggregate_trade_id=1,
        last_aggregate_trade_id=3,
    )


def _manifest(
    *,
    lag_ms: int = 25,
    source_sha256: str = "a" * 64,
) -> ClickHouseResearchCampaignManifest:
    return ClickHouseResearchCampaignManifest(
        schema_version=1,
        market="BNBUSD",
        replay_input_digest="b" * 64,
        replay_output_digest="c" * 64,
        prediction_event_count=100,
        oracle_history={"anchor": {"block_number": 1, "address": "0x" + "11" * 20}},
        assumptions={"spot_availability_lag_ms": lag_ms},
        dataset_summary={"research_feature_rows": 50, "query_end_ms": 2_000},
        spot_sources=(_slice(source_sha256),),
        perp_sources=(),
    )


def test_source_provenance_uses_final_typed_window() -> None:
    source = ProvenanceSource()
    rows = load_binance_source_slices(
        source,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="auto",
        availability_lag_ms=25,
        start_timestamp_ms=1_785_542_400_000,
        end_timestamp_ms=1_785_542_401_000,
    )
    assert len(rows) == 1
    assert rows[0].row_count == 3
    assert rows[0].source_sha256 == "a" * 64
    assert "FROM binance_agg_trades FINAL" in source.query
    assert "GROUP BY source_sha256,source_name" in source.query
    assert source.parameters == {
        "venue": "spot",
        "symbol": "BNBUSDT",
        "timestamp_unit": "auto",
        "availability_lag_ms": 25,
        "start_timestamp_ms": 1_785_542_400_000,
        "end_timestamp_ms": 1_785_542_401_000,
    }


def test_campaign_digest_is_deterministic_and_binds_latency_and_source() -> None:
    first = _manifest()
    repeated = _manifest()
    changed_lag = _manifest(lag_ms=26)
    changed_source = _manifest(source_sha256="d" * 64)
    assert first.canonical_bytes() == repeated.canonical_bytes()
    assert first.digest == repeated.digest
    assert first.digest != changed_lag.digest
    assert first.digest != changed_source.digest
    assert first.as_dict()["campaign_digest"] == first.digest
