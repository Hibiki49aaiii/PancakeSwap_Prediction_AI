from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .binance_public_rest import BinancePublicRestClient
from .event_store import EventStore
from .historical_pipeline import HistoricalPipelineConfig
from .historical_public_rpc import run_public_rpc_historical_evidence_acquisition
from .portable_features import PortableFeaturePolicy
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
        prog="ppai-historical-public",
        description=(
            "Build reconstructed Pancake Prediction research data from public logs, "
            "current static configuration, Chainlink round history, and Binance public data"
        ),
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--from-block", type=_non_negative_int, required=True)
    parser.add_argument("--to-block", type=_non_negative_int, required=True)
    parser.add_argument("--decision-lead-ns", type=_positive_int, required=True)
    parser.add_argument("--binance-latency-ns", type=_non_negative_int, required=True)
    parser.add_argument("--onchain-latency-ns", type=_non_negative_int, required=True)
    parser.add_argument("--symbol", default="BNBUSDT")
    parser.add_argument("--long-window-ns", type=_positive_int, default=30_000_000_000)
    parser.add_argument("--short-window-ns", type=_positive_int, default=5_000_000_000)
    parser.add_argument("--max-source-clock-skew-ns", type=_non_negative_int, default=2_000_000_000)
    parser.add_argument("--log-chunk-size", type=_positive_int, default=5_000)
    parser.add_argument("--binance-batch-limit", type=_positive_int, default=1_000)
    parser.add_argument("--binance-max-batches-per-window", type=_positive_int, default=10_000)
    parser.add_argument("--max-oracle-backtrack-rounds", type=_positive_int, default=512)
    parser.add_argument("--rpc-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--summary-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.to_block < args.from_block:
        raise SystemExit("--to-block must be >= --from-block")
    if not 1 <= args.binance_batch_limit <= 1_000:
        raise SystemExit("--binance-batch-limit must be in [1, 1000]")
    if args.rpc_timeout_seconds <= 0:
        raise SystemExit("--rpc-timeout-seconds must be positive")

    feature_policy = PortableFeaturePolicy(
        long_window_ns=args.long_window_ns,
        short_window_ns=args.short_window_ns,
        max_source_clock_skew_ns=args.max_source_clock_skew_ns,
    )
    config = HistoricalPipelineConfig(
        dataset_id=args.dataset_id,
        decision_lead_ns=args.decision_lead_ns,
        assumed_binance_latency_ns=args.binance_latency_ns,
        assumed_onchain_latency_ns=args.onchain_latency_ns,
        feature_policy=feature_policy,
    )
    args.store.parent.mkdir(parents=True, exist_ok=True)
    client = ReadOnlyJsonRpcClient(args.rpc_url, timeout_seconds=args.rpc_timeout_seconds)
    with EventStore(args.store, mode="reconstructed") as store:
        result = run_public_rpc_historical_evidence_acquisition(
            store,
            config=config,
            binance_client=BinancePublicRestClient(),
            rpc_client=client,
            from_block=args.from_block,
            to_block=args.to_block,
            symbol=args.symbol,
            log_chunk_size=args.log_chunk_size,
            binance_batch_limit=args.binance_batch_limit,
            binance_max_batches_per_window=args.binance_max_batches_per_window,
            max_oracle_backtrack_rounds=args.max_oracle_backtrack_rounds,
        )

    summary = {
        "schema": "public_rpc_historical_acquisition_v1",
        "dataset_id": result.dataset_id,
        "from_block": args.from_block,
        "to_block": args.to_block,
        "rpc_url": args.rpc_url,
        "static_config_head_block": result.static_config.head_block,
        "interval_seconds": result.static_config.interval_seconds,
        "treasury_fee_units": result.static_config.treasury_fee_units,
        "oracle_address": result.static_config.oracle_address,
        "oracle_decimals": result.static_config.oracle_decimals,
        "config_change_logs": result.config_change_logs,
        "completed_rounds": result.completed_rounds,
        "incomplete_epochs": list(result.incomplete_epochs),
        "bet_logs": result.bet_logs,
        "decision_snapshots": len(result.decision_snapshots.points),
        "decision_snapshots_already_present": len(result.decision_snapshots.already_present_epochs),
        "binance_windows": result.binance_windows,
        "binance_events": result.binance_events,
        "examples": result.examples,
        "skipped_examples": result.skipped_examples,
        "store_event_count": result.store_event_count,
        "store_tip_hash": result.store_tip_hash,
    }
    encoded = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    print(encoded)
    if args.summary_output is not None:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
