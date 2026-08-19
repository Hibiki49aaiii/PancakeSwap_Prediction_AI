from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .binance_ingest import poll_binance_public_once
from .binance_public_rest import BinancePublicRestClient
from .binance_websocket import BinanceMarketWebSocketIngestor, run_reconnecting_market_stream
from .event_store import EventStore
from .historical_binance import backfill_binance_aggregate_trades
from .onchain_ingest import collect_and_persist_protocol_snapshot
from .read_only_rpc import ReadOnlyJsonRpcClient


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppai-collector",
        description="Read-only PancakeSwap Prediction AI market/protocol collector",
    )
    parser.add_argument(
        "--store",
        type=Path,
        required=True,
        help="SQLite Event Store path",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rest = sub.add_parser("binance-rest-once", help="Fetch one public Binance market-data batch")
    rest.add_argument("--symbol", default="BNBUSDT")
    rest.add_argument("--trade-limit", type=_positive_int, default=1000)

    ws = sub.add_parser("binance-ws", help="Run Binance market-data-only WebSocket collection")
    ws.add_argument("--symbol", default="BNBUSDT")
    ws.add_argument("--rest-gap-repair", action=argparse.BooleanOptionalAction, default=True)
    ws.add_argument("--reconnect-delay-seconds", type=float, default=1.0)

    historical = sub.add_parser(
        "historical-binance",
        help="Backfill Binance aggTrades into a separate reconstructed Event Store",
    )
    historical.add_argument("--dataset-id", required=True)
    historical.add_argument("--symbol", default="BNBUSDT")
    historical.add_argument("--start-time-ms", type=_non_negative_int, required=True)
    historical.add_argument("--end-time-ms", type=_non_negative_int, required=True)
    historical.add_argument("--assumed-latency-ns", type=_non_negative_int, required=True)
    historical.add_argument("--batch-limit", type=_positive_int, default=1000)
    historical.add_argument("--max-batches", type=_positive_int, default=10_000)

    protocol = sub.add_parser("protocol-once", help="Collect one pinned Pancake + Chainlink BSC snapshot")
    protocol.add_argument("--rpc-url", required=True)
    protocol.add_argument("--rpc-timeout-seconds", type=float, default=10.0)

    verify = sub.add_parser("verify-store", help="Verify the Event Store hash chain")
    verify.add_argument(
        "--mode",
        choices=("observed", "reconstructed"),
        default="observed",
        help="Expected persisted availability mode",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.store.parent.mkdir(parents=True, exist_ok=True)

    if args.command == "historical-binance":
        store_mode = "reconstructed"
    elif args.command == "verify-store":
        store_mode = args.mode
    else:
        store_mode = "observed"

    with EventStore(args.store, mode=store_mode) as store:
        if args.command == "binance-rest-once":
            if not 1 <= args.trade_limit <= 1000:
                raise SystemExit("--trade-limit must be in [1, 1000]")
            result = poll_binance_public_once(
                BinancePublicRestClient(),
                store,
                symbol=args.symbol,
                trade_limit=args.trade_limit,
            )
            print(
                f"binance-rest trades={result.trades_appended} "
                f"last_agg_trade_id={result.last_aggregate_trade_id} "
                f"book_event_id={result.book_event_id}"
            )
            return 0

        if args.command == "binance-ws":
            if args.reconnect_delay_seconds < 0:
                raise SystemExit("--reconnect-delay-seconds must be non-negative")
            ingestor = BinanceMarketWebSocketIngestor(store, symbol=args.symbol)
            rest = BinancePublicRestClient() if args.rest_gap_repair else None
            run_reconnecting_market_stream(
                ingestor,
                rest=rest,
                reconnect_delay_seconds=args.reconnect_delay_seconds,
            )
            return 0

        if args.command == "historical-binance":
            if not 1 <= args.batch_limit <= 1000:
                raise SystemExit("--batch-limit must be in [1, 1000]")
            result = backfill_binance_aggregate_trades(
                BinancePublicRestClient(),
                store,
                dataset_id=args.dataset_id,
                symbol=args.symbol,
                start_time_ms=args.start_time_ms,
                end_time_ms=args.end_time_ms,
                assumed_latency_ns=args.assumed_latency_ns,
                batch_limit=args.batch_limit,
                max_batches=args.max_batches,
            )
            print(
                f"historical-binance dataset={result.dataset_id} events={result.events_appended} "
                f"first_id={result.first_aggregate_trade_id} last_id={result.last_aggregate_trade_id}"
            )
            return 0

        if args.command == "protocol-once":
            if args.rpc_timeout_seconds <= 0:
                raise SystemExit("--rpc-timeout-seconds must be positive")
            client = ReadOnlyJsonRpcClient(
                args.rpc_url,
                timeout_seconds=args.rpc_timeout_seconds,
            )
            result = collect_and_persist_protocol_snapshot(client, store)
            snapshot = result.snapshot
            print(
                f"protocol block={snapshot.anchor.number} epoch={snapshot.current_epoch} "
                f"oracle={snapshot.oracle_address} events={len(result.stored_events)}"
            )
            return 0

        if args.command == "verify-store":
            ok = store.verify_chain()
            print(
                f"event-store mode={store.mode} hash-chain: "
                + ("OK" if ok else "FAILED")
            )
            return 0 if ok else 2

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
