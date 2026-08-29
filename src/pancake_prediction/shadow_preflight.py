from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from .binance_archive import TimestampUnit
from .binance_live import (
    BinanceAggTradeSource,
    BinanceLiveError,
    LiveVenue,
    inspect_binance_live_coverage,
    latest_binance_live_cursor,
)
from .clickhouse import (
    ClickHouseError,
    ClickHouseParameterizedJsonSource,
    QueryParameter,
)
from .clickhouse_schema import (
    ClickHouseBinanceSchemaReport,
    inspect_binance_trade_schema,
)
from .collector import ReadOnlyRpc
from .contracts import CHAIN_ID_BSC, Market
from .research_dataset import BINANCE_SYMBOL_BY_MARKET
from .research_inputs import load_canonical_research_inputs
from .rpc import RpcError
from .shadow_ledger import (
    ShadowLedgerReadOnlyInspection,
    inspect_shadow_ledger_read_only,
)
from .shadow_runtime import (
    ShadowRuntimeConfig,
    build_shadow_runtime_campaign_manifest,
)
from .store import EventStore


@dataclass(frozen=True, slots=True)
class BinanceLineagePreflight:
    venue: str
    symbol: str
    timestamp_unit: str
    availability_lag_ms: int
    row_count: int
    first_trade_timestamp_ms: int | None
    last_trade_timestamp_ms: int | None
    live_row_count: int
    latest_aggregate_trade_id: int | None
    latest_source_name: str | None
    prospective_source_consistent: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ShadowRuntimePreflightReport:
    market: str
    canonical_database: str
    shadow_database: str
    ready: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...]
    replay_rounds: int
    settled_labeled_rounds: int
    active_chainlink_event_count: int
    minimum_settled_rounds: int
    minimum_replay_rounds: int
    minimum_chainlink_events: int
    last_collected_block: int | None
    oracle_proxy_anchor: str | None
    chainlink_aggregator_anchor: str | None
    expected_campaign_manifest_digest: str | None
    expected_campaign_manifest: dict[str, object] | None
    shadow_ledger: ShadowLedgerReadOnlyInspection
    bsc_chain_id: int | None
    bsc_head_block: int | None
    clickhouse_schema: ClickHouseBinanceSchemaReport | None
    spot_lineage: BinanceLineagePreflight | None
    perp_lineage: BinanceLineagePreflight | None
    binance_spot_probe_rows: int | None
    binance_perp_probe_rows: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "canonical_database": self.canonical_database,
            "shadow_database": self.shadow_database,
            "ready": self.ready,
            "checks": dict(self.checks),
            "failures": list(self.failures),
            "canonical": {
                "replay_rounds": self.replay_rounds,
                "settled_labeled_rounds": self.settled_labeled_rounds,
                "active_chainlink_event_count": self.active_chainlink_event_count,
                "minimum_settled_rounds": self.minimum_settled_rounds,
                "minimum_replay_rounds": self.minimum_replay_rounds,
                "minimum_chainlink_events": self.minimum_chainlink_events,
                "last_collected_block": self.last_collected_block,
                "oracle_proxy_anchor": self.oracle_proxy_anchor,
                "chainlink_aggregator_anchor": self.chainlink_aggregator_anchor,
            },
            "campaign_manifest": {
                "digest": self.expected_campaign_manifest_digest,
                "payload": self.expected_campaign_manifest,
            },
            "shadow_ledger": self.shadow_ledger.as_dict(),
            "bsc": {
                "chain_id": self.bsc_chain_id,
                "head_block": self.bsc_head_block,
            },
            "clickhouse": {
                "schema": (
                    None
                    if self.clickhouse_schema is None
                    else self.clickhouse_schema.as_dict()
                ),
                "spot_lineage": (
                    None if self.spot_lineage is None else self.spot_lineage.as_dict()
                ),
                "perp_lineage": (
                    None if self.perp_lineage is None else self.perp_lineage.as_dict()
                ),
            },
            "binance": {
                "spot_probe_rows": self.binance_spot_probe_rows,
                "perp_probe_rows": self.binance_perp_probe_rows,
            },
            "limitations": [
                (
                    "Structural preflight does not prove that the current live oracle route "
                    "still matches the canonical route anchor; normal runtime chain sync "
                    "proves that fail-closed before collection."
                ),
                (
                    "Structural preflight does not satisfy prospective live market-data "
                    "warmup or prove that a current target ResearchFeatureRow is inferable."
                ),
                (
                    "Configured ClickHouse lineage presence does not prove complete feature "
                    "coverage for every historical replay round."
                ),
                (
                    "Structural preflight does not establish profitability, historical-source "
                    "completeness, signing authority, or funded execution readiness."
                ),
            ],
            "signing_enabled": False,
            "live_broadcast": False,
            "funded_execution": False,
            "profitability_gate_eligible": False,
            "full_historical_gate_satisfied": False,
        }


def _valid_address(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.lower()
    if (
        normalized.startswith("0x")
        and len(normalized) == 42
        and all(char in "0123456789abcdef" for char in normalized[2:])
    ):
        return normalized
    return None


def _non_negative_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _lineage_summary(
    source: ClickHouseParameterizedJsonSource,
    *,
    market: str,
    venue: LiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
) -> BinanceLineagePreflight:
    symbol = BINANCE_SYMBOL_BY_MARKET[market]
    query = (
        "SELECT count() AS row_count,"
        "minOrNull(trade_timestamp_ms) AS first_trade_timestamp_ms,"
        "maxOrNull(trade_timestamp_ms) AS last_trade_timestamp_ms "
        "FROM binance_agg_trades FINAL WHERE "
        "venue={venue:String} AND symbol={symbol:String} AND "
        "timestamp_unit={timestamp_unit:String} AND "
        "availability_lag_ms={availability_lag_ms:UInt32}"
    )
    parameters: dict[str, QueryParameter] = {
        "venue": venue,
        "symbol": symbol,
        "timestamp_unit": timestamp_unit,
        "availability_lag_ms": availability_lag_ms,
    }
    rows = tuple(source.query_json_rows(query, parameters=parameters))
    if len(rows) != 1:
        raise ValueError("ClickHouse lineage summary must return exactly one row")
    row = rows[0]
    row_count = int(str(row["row_count"]))
    first_raw = row.get("first_trade_timestamp_ms")
    last_raw = row.get("last_trade_timestamp_ms")
    first = None if first_raw is None else int(str(first_raw))
    last = None if last_raw is None else int(str(last_raw))
    if row_count < 0:
        raise ValueError("ClickHouse lineage row count must be non-negative")
    if row_count == 0 and (first is not None or last is not None):
        raise ValueError("empty ClickHouse lineage unexpectedly has time bounds")
    if row_count > 0 and (first is None or last is None or last < first):
        raise ValueError("ClickHouse lineage time bounds are invalid")

    live_coverage = inspect_binance_live_coverage(
        source,
        market=market,
        venue=venue,
        availability_lag_ms=availability_lag_ms,
        timestamp_unit=timestamp_unit,
    )
    cursor = latest_binance_live_cursor(
        source,
        market=market,
        venue=venue,
        availability_lag_ms=availability_lag_ms,
        timestamp_unit=timestamp_unit,
    )
    if live_coverage.row_count > row_count:
        raise ValueError("Binance live coverage exceeds configured lineage size")
    if row_count == 0 and cursor is not None:
        raise ValueError("empty ClickHouse lineage unexpectedly has a latest cursor")
    if row_count > 0 and cursor is None:
        raise ValueError("non-empty ClickHouse lineage has no latest cursor")

    expected_live_source = f"binance-rest:{venue}"
    prospective_source_consistent = (
        live_coverage.row_count == 0
        or (
            cursor is not None
            and cursor.source_name == expected_live_source
        )
    )
    return BinanceLineagePreflight(
        venue=venue,
        symbol=symbol,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
        row_count=row_count,
        first_trade_timestamp_ms=first,
        last_trade_timestamp_ms=last,
        live_row_count=live_coverage.row_count,
        latest_aggregate_trade_id=(
            None if cursor is None else cursor.aggregate_trade_id
        ),
        latest_source_name=None if cursor is None else cursor.source_name,
        prospective_source_consistent=prospective_source_consistent,
    )


def _binance_probe(
    source: BinanceAggTradeSource,
    *,
    venue: LiveVenue,
    symbol: str,
) -> int:
    page = source.fetch_agg_trades(
        venue=venue,
        symbol=symbol,
        parameters={"limit": 1},
    )
    return len(page.rows)


def run_shadow_runtime_preflight(
    rpc: ReadOnlyRpc,
    clickhouse: ClickHouseParameterizedJsonSource,
    binance: BinanceAggTradeSource,
    market: Market,
    canonical_database: Path,
    shadow_database: Path,
    *,
    config: ShadowRuntimeConfig | None = None,
) -> ShadowRuntimePreflightReport:
    selected = config or ShadowRuntimeConfig()
    selected.validate()
    if market.symbol not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market.symbol}")

    model_history = (
        selected.inference.min_train_rounds
        + selected.inference.calibration_rounds
    )
    minimum_settled_rounds = max(
        model_history,
        selected.inference.pool_min_train_rounds,
    )
    minimum_replay_rounds = (
        minimum_settled_rounds
        + selected.inference.purge_rounds
        + 1
    )
    minimum_chainlink_events = selected.oracle_hazard_min_intervals + 1

    checks: dict[str, bool] = {
        "canonical_database_exists": canonical_database.is_file(),
    }

    replay_rounds = 0
    settled_labeled_rounds = 0
    active_chainlink_event_count = 0
    last_collected_block: int | None = None
    oracle_proxy_anchor: str | None = None
    chainlink_aggregator_anchor: str | None = None

    if checks["canonical_database_exists"]:
        try:
            store = EventStore(canonical_database)
            last_collected_block = _non_negative_int(
                store.metadata(f"{market.symbol}.last_collected_block")
            )
            oracle_proxy_anchor = _valid_address(
                store.metadata(f"{market.symbol}.oracle_proxy_anchor_address")
            )
            chainlink_aggregator_anchor = _valid_address(
                store.metadata(f"{market.symbol}.oracle_anchor_address")
            )
            checks["last_collected_block_valid"] = last_collected_block is not None
            checks["oracle_proxy_anchor_valid"] = oracle_proxy_anchor is not None
            checks["chainlink_aggregator_anchor_valid"] = (
                chainlink_aggregator_anchor is not None
            )
        except sqlite3.Error:
            checks["last_collected_block_valid"] = False
            checks["oracle_proxy_anchor_valid"] = False
            checks["chainlink_aggregator_anchor_valid"] = False

        try:
            inputs = load_canonical_research_inputs(
                canonical_database,
                market.symbol,
            )
            replay_rounds = len(inputs.replay.rounds)
            settled_labeled_rounds = sum(
                1
                for record in inputs.replay.rounds
                if record.label in {"bull", "bear"}
                and record.end_timestamp is not None
            )
            active_chainlink_event_count = len(inputs.oracle_history.events)
            checks["canonical_inputs_loadable"] = True
        except (sqlite3.Error, ValueError):
            checks["canonical_inputs_loadable"] = False
    else:
        checks["last_collected_block_valid"] = False
        checks["oracle_proxy_anchor_valid"] = False
        checks["chainlink_aggregator_anchor_valid"] = False
        checks["canonical_inputs_loadable"] = False

    expected_campaign_manifest_digest: str | None = None
    expected_campaign_manifest: dict[str, object] | None = None
    if oracle_proxy_anchor is not None and chainlink_aggregator_anchor is not None:
        try:
            manifest = build_shadow_runtime_campaign_manifest(
                market,
                oracle_proxy_anchor=oracle_proxy_anchor,
                chainlink_aggregator_anchor=chainlink_aggregator_anchor,
                config=selected,
            )
            expected_campaign_manifest_digest = manifest.digest
            expected_campaign_manifest = manifest.canonical_payload()
            checks["campaign_manifest_constructible"] = True
        except ValueError:
            checks["campaign_manifest_constructible"] = False
    else:
        checks["campaign_manifest_constructible"] = False

    shadow_ledger = inspect_shadow_ledger_read_only(shadow_database)
    if shadow_ledger.binding_state in {"absent", "empty_unbound"}:
        checks["shadow_campaign_compatible"] = True
    elif shadow_ledger.binding_state == "bound":
        checks["shadow_campaign_compatible"] = (
            expected_campaign_manifest_digest is not None
            and expected_campaign_manifest is not None
            and shadow_ledger.manifest_digest
            == expected_campaign_manifest_digest
            and shadow_ledger.manifest_payload == expected_campaign_manifest
        )
    else:
        checks["shadow_campaign_compatible"] = False

    checks["replay_present"] = replay_rounds > 0
    checks["settled_history_capacity"] = (
        settled_labeled_rounds >= minimum_settled_rounds
    )
    checks["replay_capacity"] = replay_rounds >= minimum_replay_rounds
    checks["chainlink_history_capacity"] = (
        active_chainlink_event_count >= minimum_chainlink_events
    )

    bsc_chain_id: int | None = None
    bsc_head_block: int | None = None
    try:
        bsc_chain_id = rpc.chain_id()
        checks["bsc_chain_id"] = bsc_chain_id == CHAIN_ID_BSC
    except (RpcError, ValueError):
        checks["bsc_chain_id"] = False
    try:
        bsc_head_block = rpc.block_number()
        checks["bsc_head_not_behind_canonical"] = (
            last_collected_block is not None
            and bsc_head_block >= last_collected_block
        )
    except (RpcError, ValueError):
        checks["bsc_head_not_behind_canonical"] = False

    schema: ClickHouseBinanceSchemaReport | None = None
    spot_lineage: BinanceLineagePreflight | None = None
    perp_lineage: BinanceLineagePreflight | None = None
    try:
        schema = inspect_binance_trade_schema(clickhouse)
        checks["clickhouse_schema_ready"] = schema.ready
    except (ClickHouseError, ValueError):
        checks["clickhouse_schema_ready"] = False

    if checks["clickhouse_schema_ready"]:
        try:
            spot_lineage = _lineage_summary(
                clickhouse,
                market=market.symbol,
                venue="spot",
                timestamp_unit=selected.spot_timestamp_unit,
                availability_lag_ms=selected.spot_availability_lag_ms,
            )
            checks["spot_lineage_present"] = spot_lineage.row_count > 0
            checks["spot_lineage_source_consistent"] = (
                spot_lineage.prospective_source_consistent
            )
        except (ClickHouseError, KeyError, ValueError):
            checks["spot_lineage_present"] = False
            checks["spot_lineage_source_consistent"] = False
        if selected.include_perp:
            try:
                perp_lineage = _lineage_summary(
                    clickhouse,
                    market=market.symbol,
                    venue="um_futures",
                    timestamp_unit=selected.perp_timestamp_unit,
                    availability_lag_ms=selected.perp_availability_lag_ms,
                )
                checks["perp_lineage_present"] = perp_lineage.row_count > 0
                checks["perp_lineage_source_consistent"] = (
                    perp_lineage.prospective_source_consistent
                )
            except (ClickHouseError, KeyError, ValueError):
                checks["perp_lineage_present"] = False
                checks["perp_lineage_source_consistent"] = False
        else:
            checks["perp_lineage_present"] = True
            checks["perp_lineage_source_consistent"] = True
    else:
        checks["spot_lineage_present"] = False
        checks["spot_lineage_source_consistent"] = False
        checks["perp_lineage_present"] = not selected.include_perp
        checks["perp_lineage_source_consistent"] = not selected.include_perp

    symbol = BINANCE_SYMBOL_BY_MARKET[market.symbol]
    binance_spot_probe_rows: int | None = None
    binance_perp_probe_rows: int | None = None
    try:
        binance_spot_probe_rows = _binance_probe(
            binance,
            venue="spot",
            symbol=symbol,
        )
        checks["binance_spot_reachable"] = binance_spot_probe_rows > 0
    except (BinanceLiveError, ValueError):
        checks["binance_spot_reachable"] = False

    if selected.include_perp:
        try:
            binance_perp_probe_rows = _binance_probe(
                binance,
                venue="um_futures",
                symbol=symbol,
            )
            checks["binance_perp_reachable"] = binance_perp_probe_rows > 0
        except (BinanceLiveError, ValueError):
            checks["binance_perp_reachable"] = False
    else:
        checks["binance_perp_reachable"] = True

    failures = tuple(name for name, passed in checks.items() if not passed)
    return ShadowRuntimePreflightReport(
        market=market.symbol,
        canonical_database=str(canonical_database),
        shadow_database=str(shadow_database),
        ready=not failures,
        checks=dict(checks),
        failures=failures,
        replay_rounds=replay_rounds,
        settled_labeled_rounds=settled_labeled_rounds,
        active_chainlink_event_count=active_chainlink_event_count,
        minimum_settled_rounds=minimum_settled_rounds,
        minimum_replay_rounds=minimum_replay_rounds,
        minimum_chainlink_events=minimum_chainlink_events,
        last_collected_block=last_collected_block,
        oracle_proxy_anchor=oracle_proxy_anchor,
        chainlink_aggregator_anchor=chainlink_aggregator_anchor,
        expected_campaign_manifest_digest=expected_campaign_manifest_digest,
        expected_campaign_manifest=expected_campaign_manifest,
        shadow_ledger=shadow_ledger,
        bsc_chain_id=bsc_chain_id,
        bsc_head_block=bsc_head_block,
        clickhouse_schema=schema,
        spot_lineage=spot_lineage,
        perp_lineage=perp_lineage,
        binance_spot_probe_rows=binance_spot_probe_rows,
        binance_perp_probe_rows=binance_perp_probe_rows,
    )
