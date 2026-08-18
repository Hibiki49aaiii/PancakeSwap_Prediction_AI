from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .binance_archive import ArchiveVenue, TimestampUnit
from .campaign_evaluation import (
    EconomicCampaignConfig,
    run_source_bound_economic_campaign,
)
from .clickhouse import ClickHouseHttpClient, ingest_binance_archive, load_binance_trade_window
from .clickhouse_dataset import (
    ChunkedResearchDatasetBuildResult,
    build_chunked_clickhouse_research_dataset,
)
from .clickhouse_manifest import (
    ClickHouseResearchCampaignManifest,
    build_clickhouse_campaign_manifest,
)
from .clickhouse_schema import ClickHouseBinanceSchemaReport, inspect_binance_trade_schema
from .contracts import MARKETS
from .research_inputs import CanonicalResearchInputs, load_canonical_research_inputs

_TIMESTAMP_UNITS = ("auto", "milliseconds", "microseconds")


@dataclass(frozen=True, slots=True)
class _DatasetCampaignBundle:
    inputs: CanonicalResearchInputs
    assumptions: dict[str, object]
    dataset: ChunkedResearchDatasetBuildResult
    manifest: ClickHouseResearchCampaignManifest


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _client_or_error(parser: argparse.ArgumentParser) -> ClickHouseHttpClient:
    endpoint = os.environ.get("CLICKHOUSE_URL")
    if not endpoint:
        parser.error("command requires CLICKHOUSE_URL")
    return ClickHouseHttpClient(
        endpoint,
        database=os.environ.get("CLICKHOUSE_DATABASE", "default"),
        username=os.environ.get("CLICKHOUSE_USER"),
        password=os.environ.get("CLICKHOUSE_PASSWORD"),
    )


def _schema_or_error(
    parser: argparse.ArgumentParser,
    client: ClickHouseHttpClient,
) -> ClickHouseBinanceSchemaReport:
    report = inspect_binance_trade_schema(client)
    if not report.ready:
        parser.error(
            "binance_agg_trades schema is not retry-safe; apply sql/clickhouse/v0_7_core.sql "
            "to a fresh/migrated table before research IO"
        )
    return report


def _add_dataset_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market", choices=sorted(MARKETS), required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--spot-timestamp-unit", choices=_TIMESTAMP_UNITS, default="auto")
    parser.add_argument("--spot-availability-lag-ms", type=int, required=True)
    parser.add_argument(
        "--perp-timestamp-unit",
        choices=_TIMESTAMP_UNITS,
        default="milliseconds",
    )
    parser.add_argument("--perp-availability-lag-ms", type=int, default=0)
    parser.add_argument("--no-perp", action="store_true")
    parser.add_argument("--chunk-span-ms", type=int, default=3_600_000)
    parser.add_argument("--feature-lead-seconds", type=int, default=20)
    parser.add_argument("--flow-lookback-ms", type=int, default=60_000)
    parser.add_argument("--max-spot-age-ms", type=int, default=5_000)
    parser.add_argument("--max-perp-age-ms", type=int, default=5_000)
    parser.add_argument("--max-chainlink-age-ms", type=int, default=None)
    parser.add_argument("--chainlink-availability-lag-ms", type=int, default=0)
    parser.add_argument("--oracle-history-updates", type=int, default=512)
    parser.add_argument("--oracle-hazard-horizon-ms", type=int, default=5_000)
    parser.add_argument("--oracle-hazard-min-intervals", type=int, default=8)


def _add_economic_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stake-wei", type=int, required=True)
    parser.add_argument("--bet-gas-wei", type=int, required=True)
    parser.add_argument("--claim-gas-wei", type=int, required=True)
    parser.add_argument("--inclusion-latency-seconds", type=int, required=True)
    parser.add_argument("--min-expected-value-wei", type=int, default=0)
    parser.add_argument("--initial-interval-seconds", type=int, default=300)
    parser.add_argument("--initial-treasury-fee-bps", type=int, default=300)
    parser.add_argument("--initial-buffer-seconds", type=int, default=30)
    parser.add_argument("--min-train-rounds", type=int, default=200)
    parser.add_argument("--test-rounds", type=int, default=100)
    parser.add_argument("--purge-rounds", type=int, default=2)
    parser.add_argument("--embargo-rounds", type=int, default=2)
    parser.add_argument("--calibration-rounds", type=int, default=50)
    parser.add_argument("--pool-min-train-rounds", type=int, default=50)
    parser.add_argument("--pool-window-rounds", type=int, default=500)
    parser.add_argument("--run-ablation", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcs-clickhouse",
        description="Bounded-memory ClickHouse research-data tooling.",
    )
    subparsers = parser.add_subparsers(dest="command")

    ping = subparsers.add_parser("ping", help="verify ClickHouse connectivity")
    ping.set_defaults(command="ping")

    subparsers.add_parser(
        "schema-check",
        help="validate the retry-safe Binance research table before ingest/query",
    )

    ingest = subparsers.add_parser(
        "binance-ingest",
        help="verify and stream one Binance aggTrades archive into ClickHouse",
    )
    ingest.add_argument("--market", choices=sorted(MARKETS), required=True)
    ingest.add_argument("--archive", type=Path, required=True)
    ingest.add_argument("--checksum", type=Path, required=True)
    ingest.add_argument("--venue", choices=("spot", "um_futures"), required=True)
    ingest.add_argument(
        "--timestamp-unit",
        choices=_TIMESTAMP_UNITS,
        default="auto",
    )
    ingest.add_argument("--availability-lag-ms", type=int, required=True)
    ingest.add_argument("--batch-size", type=int, default=50_000)

    window = subparsers.add_parser(
        "binance-window",
        help="read one deduplicated Binance research window from ClickHouse",
    )
    window.add_argument("--market", choices=sorted(MARKETS), required=True)
    window.add_argument("--venue", choices=("spot", "um_futures"), required=True)
    window.add_argument("--timestamp-unit", choices=_TIMESTAMP_UNITS, required=True)
    window.add_argument("--availability-lag-ms", type=int, required=True)
    window.add_argument("--start-ms", type=int, required=True)
    window.add_argument("--end-ms", type=int, required=True)

    dataset = subparsers.add_parser(
        "dataset-summary",
        help="build a canonical research dataset from SQLite plus chunked ClickHouse windows",
    )
    _add_dataset_arguments(dataset)

    evaluate = subparsers.add_parser(
        "campaign-evaluate",
        help="run source-bound purged OOS and cost-aware economic validation",
    )
    _add_dataset_arguments(evaluate)
    _add_economic_arguments(evaluate)
    return parser


def _dataset_assumptions(args: argparse.Namespace) -> dict[str, object]:
    return {
        "spot_timestamp_unit": str(args.spot_timestamp_unit),
        "spot_availability_lag_ms": int(args.spot_availability_lag_ms),
        "perp_timestamp_unit": str(args.perp_timestamp_unit),
        "perp_availability_lag_ms": int(args.perp_availability_lag_ms),
        "include_perp": not bool(args.no_perp),
        "chunk_span_ms": int(args.chunk_span_ms),
        "feature_lead_seconds": int(args.feature_lead_seconds),
        "flow_lookback_ms": int(args.flow_lookback_ms),
        "max_spot_age_ms": int(args.max_spot_age_ms),
        "max_perp_age_ms": int(args.max_perp_age_ms),
        "max_chainlink_age_ms": (
            None if args.max_chainlink_age_ms is None else int(args.max_chainlink_age_ms)
        ),
        "chainlink_availability_lag_ms": int(args.chainlink_availability_lag_ms),
        "oracle_history_updates": int(args.oracle_history_updates),
        "oracle_hazard_horizon_ms": int(args.oracle_hazard_horizon_ms),
        "oracle_hazard_min_intervals": int(args.oracle_hazard_min_intervals),
    }


def _build_dataset_bundle(
    args: argparse.Namespace,
    client: ClickHouseHttpClient,
) -> _DatasetCampaignBundle:
    market = str(args.market)
    inputs = load_canonical_research_inputs(Path(args.db), market)
    assumptions = _dataset_assumptions(args)
    spot_timestamp_unit = cast(TimestampUnit, str(args.spot_timestamp_unit))
    perp_timestamp_unit = cast(TimestampUnit, str(args.perp_timestamp_unit))
    include_perp = not bool(args.no_perp)
    dataset_result = build_chunked_clickhouse_research_dataset(
        inputs.replay,
        inputs.events,
        client,
        spot_timestamp_unit=spot_timestamp_unit,
        spot_availability_lag_ms=int(args.spot_availability_lag_ms),
        perp_timestamp_unit=perp_timestamp_unit,
        perp_availability_lag_ms=int(args.perp_availability_lag_ms),
        include_perp=include_perp,
        chunk_span_ms=int(args.chunk_span_ms),
        feature_lead_seconds=int(args.feature_lead_seconds),
        flow_lookback_ms=int(args.flow_lookback_ms),
        max_spot_age_ms=int(args.max_spot_age_ms),
        max_perp_age_ms=int(args.max_perp_age_ms),
        max_chainlink_age_ms=(
            None if args.max_chainlink_age_ms is None else int(args.max_chainlink_age_ms)
        ),
        chainlink_availability_lag_ms=int(args.chainlink_availability_lag_ms),
        oracle_history_updates=int(args.oracle_history_updates),
        oracle_hazard_horizon_ms=int(args.oracle_hazard_horizon_ms),
        oracle_hazard_min_intervals=int(args.oracle_hazard_min_intervals),
    )
    campaign_manifest = build_clickhouse_campaign_manifest(
        client,
        inputs,
        dataset_result,
        assumptions,
        spot_timestamp_unit=spot_timestamp_unit,
        spot_availability_lag_ms=int(args.spot_availability_lag_ms),
        perp_timestamp_unit=perp_timestamp_unit,
        perp_availability_lag_ms=int(args.perp_availability_lag_ms),
        include_perp=include_perp,
    )
    return _DatasetCampaignBundle(
        inputs=inputs,
        assumptions=assumptions,
        dataset=dataset_result,
        manifest=campaign_manifest,
    )


def _economic_config(args: argparse.Namespace) -> EconomicCampaignConfig:
    return EconomicCampaignConfig(
        stake_wei=int(args.stake_wei),
        bet_gas_wei=int(args.bet_gas_wei),
        claim_gas_wei=int(args.claim_gas_wei),
        inclusion_latency_seconds=int(args.inclusion_latency_seconds),
        min_expected_value_wei=int(args.min_expected_value_wei),
        decision_lead_seconds=int(args.feature_lead_seconds),
        initial_interval_seconds=int(args.initial_interval_seconds),
        initial_treasury_fee_bps=int(args.initial_treasury_fee_bps),
        initial_buffer_seconds=int(args.initial_buffer_seconds),
        min_train_rounds=int(args.min_train_rounds),
        test_rounds=int(args.test_rounds),
        purge_rounds=int(args.purge_rounds),
        embargo_rounds=int(args.embargo_rounds),
        calibration_rounds=int(args.calibration_rounds),
        pool_min_train_rounds=int(args.pool_min_train_rounds),
        pool_window_rounds=int(args.pool_window_rounds),
        run_ablation=bool(args.run_ablation),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    client = _client_or_error(parser)
    if args.command == "ping":
        value = client.execute("SELECT 1").strip()
        _print_json({"ok": value == "1"})
        return 0 if value == "1" else 2

    if args.command == "schema-check":
        schema_report = inspect_binance_trade_schema(client)
        _print_json(schema_report.as_dict())
        return 0 if schema_report.ready else 2

    if args.command == "binance-ingest":
        _schema_or_error(parser, client)
        ingest_report = ingest_binance_archive(
            client,
            Path(args.archive),
            Path(args.checksum),
            market=str(args.market),
            venue=cast(ArchiveVenue, str(args.venue)),
            timestamp_unit=cast(TimestampUnit, str(args.timestamp_unit)),
            availability_lag_ms=int(args.availability_lag_ms),
            batch_size=int(args.batch_size),
        )
        _print_json(ingest_report.as_dict())
        return 0

    if args.command == "binance-window":
        _schema_or_error(parser, client)
        trades = load_binance_trade_window(
            client,
            market=str(args.market),
            venue=cast(ArchiveVenue, str(args.venue)),
            timestamp_unit=cast(TimestampUnit, str(args.timestamp_unit)),
            availability_lag_ms=int(args.availability_lag_ms),
            start_timestamp_ms=int(args.start_ms),
            end_timestamp_ms=int(args.end_ms),
        )
        _print_json(
            {
                "market": str(args.market),
                "venue": str(args.venue),
                "timestamp_unit": str(args.timestamp_unit),
                "rows": len(trades),
                "first_aggregate_trade_id": (
                    None if not trades else trades[0].aggregate_trade_id
                ),
                "last_aggregate_trade_id": (
                    None if not trades else trades[-1].aggregate_trade_id
                ),
            }
        )
        return 0

    if args.command in {"dataset-summary", "campaign-evaluate"}:
        _schema_or_error(parser, client)
        bundle = _build_dataset_bundle(args, client)
        common_payload: dict[str, object] = {
            "inputs": bundle.inputs.as_dict(),
            "assumptions": bundle.assumptions,
            "dataset": bundle.dataset.as_dict(),
            "campaign_manifest": bundle.manifest.as_dict(),
        }
        if args.command == "dataset-summary":
            _print_json(common_payload)
            return 0
        evaluation = run_source_bound_economic_campaign(
            bundle.inputs.replay,
            bundle.inputs.events,
            bundle.dataset.dataset.research_feature_rows,
            campaign_digest=bundle.manifest.digest,
            config=_economic_config(args),
        )
        common_payload["evaluation"] = evaluation.as_dict()
        _print_json(common_payload)
        return 0

    parser.error(f"unsupported command: {args.command}")
