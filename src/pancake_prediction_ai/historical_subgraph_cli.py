from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .binance_public_rest import BinancePublicRestClient
from .event_store import EventStore
from .historical_pipeline import HistoricalPipelineConfig
from .historical_subgraph import run_subgraph_historical_evidence_acquisition
from .portable_features import PortableFeaturePolicy
from .prediction_subgraph import PREDICTION_V2_SUBGRAPH_URL, PredictionSubgraphClient
from .read_only_rpc import ReadOnlyJsonRpcClient


GRAPH_API_KEY_ENV = "GRAPH_API_KEY"


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
        prog="ppai-historical-subgraph",
        description=(
            "Build leakage-safe reconstructed Pancake Prediction history from the official "
            "Prediction V2 Subgraph, Binance public data, and read-only BSC config/oracle proof"
        ),
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--from-epoch", type=_non_negative_int, required=True)
    parser.add_argument("--to-epoch", type=_non_negative_int, required=True)
    parser.add_argument("--decision-lead-ns", type=_positive_int, required=True)
    parser.add_argument("--binance-latency-ns", type=_non_negative_int, required=True)
    parser.add_argument("--onchain-latency-ns", type=_non_negative_int, required=True)
    parser.add_argument("--subgraph-latency-ns", type=_non_negative_int, required=True)
    parser.add_argument("--symbol", default="BNBUSDT")
    parser.add_argument("--subgraph-url", default=PREDICTION_V2_SUBGRAPH_URL)
    parser.add_argument("--subgraph-page-size", type=_positive_int, default=500)
    parser.add_argument("--config-log-chunk-size", type=_positive_int, default=50)
    parser.add_argument("--binance-batch-limit", type=_positive_int, default=1_000)
    parser.add_argument("--binance-max-batches-per-window", type=_positive_int, default=10_000)
    parser.add_argument("--max-oracle-backtrack-rounds", type=_positive_int, default=512)
    parser.add_argument("--long-window-ns", type=_positive_int, default=30_000_000_000)
    parser.add_argument("--short-window-ns", type=_positive_int, default=5_000_000_000)
    parser.add_argument("--max-source-clock-skew-ns", type=_non_negative_int, default=2_000_000_000)
    parser.add_argument("--rpc-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--subgraph-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--dataset-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.to_epoch < args.from_epoch:
        raise SystemExit("--to-epoch must be >= --from-epoch")
    if not 1 <= args.subgraph_page_size <= 1_000:
        raise SystemExit("--subgraph-page-size must be in [1, 1000]")
    if not 1 <= args.binance_batch_limit <= 1_000:
        raise SystemExit("--binance-batch-limit must be in [1, 1000]")
    if args.rpc_timeout_seconds <= 0 or args.subgraph_timeout_seconds <= 0:
        raise SystemExit("RPC/subgraph timeouts must be positive")

    api_key = os.environ.get(GRAPH_API_KEY_ENV, "").strip()
    if not api_key:
        raise SystemExit(
            f"{GRAPH_API_KEY_ENV} is required; keep The Graph API key in the environment/CI secret, not CLI arguments"
        )

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
        assumed_subgraph_latency_ns=args.subgraph_latency_ns,
        feature_policy=feature_policy,
    )
    args.store.parent.mkdir(parents=True, exist_ok=True)
    args.dataset_output.parent.mkdir(parents=True, exist_ok=True)
    if args.summary_output is not None:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)

    subgraph = PredictionSubgraphClient(
        api_key=api_key,
        endpoint=args.subgraph_url,
        timeout_seconds=args.subgraph_timeout_seconds,
    )
    rpc = ReadOnlyJsonRpcClient(
        args.rpc_url,
        timeout_seconds=args.rpc_timeout_seconds,
    )
    with EventStore(args.store, mode="reconstructed") as store:
        result = run_subgraph_historical_evidence_acquisition(
            store,
            config=config,
            subgraph_client=subgraph,
            binance_client=BinancePublicRestClient(),
            rpc_client=rpc,
            from_epoch=args.from_epoch,
            to_epoch=args.to_epoch,
            assumed_subgraph_latency_ns=args.subgraph_latency_ns,
            symbol=args.symbol,
            subgraph_page_size=args.subgraph_page_size,
            config_log_chunk_size=args.config_log_chunk_size,
            binance_batch_limit=args.binance_batch_limit,
            binance_max_batches_per_window=args.binance_max_batches_per_window,
            max_oracle_backtrack_rounds=args.max_oracle_backtrack_rounds,
        )
        # Reopen semantics are encoded in HistoricalPipeline metadata bindings;
        # freeze immediately as well so this run's source assumptions are explicit.
        from .historical_pipeline import HistoricalPipeline

        artifact = HistoricalPipeline(store, config).build_dataset_artifact()
        artifact.write(args.dataset_output)

        summary = {
            "dataset_id": result.dataset_id,
            "subgraph_id": subgraph.endpoint.rsplit("/", 1)[-1],
            "subgraph_meta_block": result.meta.block_number,
            "subgraph_has_indexing_errors": result.meta.has_indexing_errors,
            "config_proof_from_block": result.config_proof.from_block,
            "config_proof_through_block": result.config_proof.through_block,
            "config_change_logs": result.config_proof.config_change_logs,
            "interval_seconds": result.config_proof.static_config.interval_seconds,
            "treasury_fee_units": result.config_proof.static_config.treasury_fee_units,
            "oracle_address": result.config_proof.static_config.oracle_address,
            "rounds_fetched": result.rounds_fetched,
            "completed_rounds": result.completed_rounds,
            "indexed_bets": result.indexed_bets,
            "decision_snapshots": len(result.decision_snapshots.points),
            "binance_windows": result.binance_windows,
            "binance_events": result.binance_events,
            "examples": result.examples,
            "skipped_examples": result.skipped_examples,
            "store_event_count": result.store_event_count,
            "store_tip_hash": result.store_tip_hash,
            "dataset_artifact_sha256": artifact.artifact_sha256,
            "assumed_subgraph_latency_ns": args.subgraph_latency_ns,
        }
        if args.summary_output is not None:
            args.summary_output.write_text(
                json.dumps(summary, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
