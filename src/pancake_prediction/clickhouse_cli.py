from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from .binance_archive import ArchiveVenue, TimestampUnit
from .clickhouse import ClickHouseHttpClient, ingest_binance_archive, load_binance_trade_window
from .clickhouse_schema import ClickHouseBinanceSchemaReport, inspect_binance_trade_schema
from .contracts import MARKETS


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
        choices=("auto", "milliseconds", "microseconds"),
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
    window.add_argument("--availability-lag-ms", type=int, required=True)
    window.add_argument("--start-ms", type=int, required=True)
    window.add_argument("--end-ms", type=int, required=True)
    return parser


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
        report = inspect_binance_trade_schema(client)
        _print_json(report.as_dict())
        return 0 if report.ready else 2

    if args.command == "binance-ingest":
        _schema_or_error(parser, client)
        report = ingest_binance_archive(
            client,
            Path(args.archive),
            Path(args.checksum),
            market=str(args.market),
            venue=cast(ArchiveVenue, str(args.venue)),
            timestamp_unit=cast(TimestampUnit, str(args.timestamp_unit)),
            availability_lag_ms=int(args.availability_lag_ms),
            batch_size=int(args.batch_size),
        )
        _print_json(report.as_dict())
        return 0

    if args.command == "binance-window":
        _schema_or_error(parser, client)
        trades = load_binance_trade_window(
            client,
            market=str(args.market),
            venue=cast(ArchiveVenue, str(args.venue)),
            availability_lag_ms=int(args.availability_lag_ms),
            start_timestamp_ms=int(args.start_ms),
            end_timestamp_ms=int(args.end_ms),
        )
        _print_json(
            {
                "market": str(args.market),
                "venue": str(args.venue),
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

    parser.error(f"unsupported command: {args.command}")
