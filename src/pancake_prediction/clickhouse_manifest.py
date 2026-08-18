from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .binance_archive import ArchiveVenue, TimestampUnit
from .clickhouse import ClickHouseParameterizedJsonSource, QueryParameter
from .clickhouse_dataset import ChunkedResearchDatasetBuildResult
from .research_dataset import BINANCE_SYMBOL_BY_MARKET
from .research_inputs import CanonicalResearchInputs


@dataclass(frozen=True, slots=True)
class BinanceSourceSlice:
    venue: ArchiveVenue
    symbol: str
    timestamp_unit: TimestampUnit
    availability_lag_ms: int
    source_sha256: str
    source_name: str
    row_count: int
    first_trade_timestamp_ms: int
    last_trade_timestamp_ms: int
    first_aggregate_trade_id: int
    last_aggregate_trade_id: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _validate_sha256(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("ClickHouse source_sha256 must be a SHA-256 hex digest")
    return normalized


def load_binance_source_slices(
    source: ClickHouseParameterizedJsonSource,
    *,
    market: str,
    venue: ArchiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
    start_timestamp_ms: int,
    end_timestamp_ms: int,
) -> tuple[BinanceSourceSlice, ...]:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    if start_timestamp_ms < 0 or end_timestamp_ms <= start_timestamp_ms:
        raise ValueError("invalid provenance window")
    symbol = BINANCE_SYMBOL_BY_MARKET[market]
    query = (
        "SELECT source_sha256,source_name,count() AS row_count,"
        "min(trade_timestamp_ms) AS first_trade_timestamp_ms,"
        "max(trade_timestamp_ms) AS last_trade_timestamp_ms,"
        "min(aggregate_trade_id) AS first_aggregate_trade_id,"
        "max(aggregate_trade_id) AS last_aggregate_trade_id "
        "FROM binance_agg_trades FINAL WHERE "
        "venue={venue:String} AND symbol={symbol:String} AND "
        "timestamp_unit={timestamp_unit:String} AND "
        "availability_lag_ms={availability_lag_ms:UInt32} AND "
        "trade_timestamp_ms>={start_timestamp_ms:UInt64} AND "
        "trade_timestamp_ms<{end_timestamp_ms:UInt64} "
        "GROUP BY source_sha256,source_name ORDER BY "
        "first_trade_timestamp_ms,source_sha256,source_name"
    )
    parameters: dict[str, QueryParameter] = {
        "venue": venue,
        "symbol": symbol,
        "timestamp_unit": timestamp_unit,
        "availability_lag_ms": availability_lag_ms,
        "start_timestamp_ms": start_timestamp_ms,
        "end_timestamp_ms": end_timestamp_ms,
    }
    result: list[BinanceSourceSlice] = []
    for row in source.query_json_rows(query, parameters=parameters):
        row_count = int(str(row["row_count"]))
        if row_count <= 0:
            raise ValueError("ClickHouse source provenance row_count must be positive")
        result.append(
            BinanceSourceSlice(
                venue=venue,
                symbol=symbol,
                timestamp_unit=timestamp_unit,
                availability_lag_ms=availability_lag_ms,
                source_sha256=_validate_sha256(str(row["source_sha256"])),
                source_name=str(row["source_name"]),
                row_count=row_count,
                first_trade_timestamp_ms=int(str(row["first_trade_timestamp_ms"])),
                last_trade_timestamp_ms=int(str(row["last_trade_timestamp_ms"])),
                first_aggregate_trade_id=int(str(row["first_aggregate_trade_id"])),
                last_aggregate_trade_id=int(str(row["last_aggregate_trade_id"])),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ClickHouseResearchCampaignManifest:
    schema_version: int
    market: str
    replay_input_digest: str
    replay_output_digest: str
    prediction_event_count: int
    oracle_history: Mapping[str, object]
    assumptions: Mapping[str, object]
    dataset_summary: Mapping[str, object]
    spot_sources: tuple[BinanceSourceSlice, ...]
    perp_sources: tuple[BinanceSourceSlice, ...]

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "market": self.market,
            "replay_input_digest": self.replay_input_digest,
            "replay_output_digest": self.replay_output_digest,
            "prediction_event_count": self.prediction_event_count,
            "oracle_history": dict(self.oracle_history),
            "assumptions": dict(self.assumptions),
            "dataset_summary": dict(self.dataset_summary),
            "spot_sources": [item.as_dict() for item in self.spot_sources],
            "perp_sources": [item.as_dict() for item in self.perp_sources],
        }

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def as_dict(self) -> dict[str, object]:
        payload = self.payload()
        payload["campaign_digest"] = self.digest
        return payload


def build_clickhouse_campaign_manifest(
    source: ClickHouseParameterizedJsonSource,
    inputs: CanonicalResearchInputs,
    dataset: ChunkedResearchDatasetBuildResult,
    assumptions: Mapping[str, object],
    *,
    spot_timestamp_unit: TimestampUnit,
    spot_availability_lag_ms: int,
    perp_timestamp_unit: TimestampUnit,
    perp_availability_lag_ms: int,
    include_perp: bool,
) -> ClickHouseResearchCampaignManifest:
    spot_sources: tuple[BinanceSourceSlice, ...] = ()
    perp_sources: tuple[BinanceSourceSlice, ...] = ()
    if dataset.spot_query_start_ms is not None and dataset.query_end_ms is not None:
        spot_sources = load_binance_source_slices(
            source,
            market=inputs.market,
            venue="spot",
            timestamp_unit=spot_timestamp_unit,
            availability_lag_ms=spot_availability_lag_ms,
            start_timestamp_ms=dataset.spot_query_start_ms,
            end_timestamp_ms=dataset.query_end_ms,
        )
    if (
        include_perp
        and dataset.perp_query_start_ms is not None
        and dataset.query_end_ms is not None
    ):
        perp_sources = load_binance_source_slices(
            source,
            market=inputs.market,
            venue="um_futures",
            timestamp_unit=perp_timestamp_unit,
            availability_lag_ms=perp_availability_lag_ms,
            start_timestamp_ms=dataset.perp_query_start_ms,
            end_timestamp_ms=dataset.query_end_ms,
        )
    return ClickHouseResearchCampaignManifest(
        schema_version=1,
        market=inputs.market,
        replay_input_digest=inputs.replay.input_digest,
        replay_output_digest=inputs.replay.output_digest,
        prediction_event_count=inputs.prediction_event_count,
        oracle_history=inputs.oracle_history.as_dict(),
        assumptions=dict(assumptions),
        dataset_summary=dataset.as_dict(),
        spot_sources=spot_sources,
        perp_sources=perp_sources,
    )
