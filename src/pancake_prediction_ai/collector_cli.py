from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .binance_ingest import poll_binance_public_once
from .binance_public_rest import BinancePublicRestClient
from .binance_websocket import BinanceMarketWebSocketIngestor, run_reconnecting_market_stream
from .event_store import EventStore
from .historical_binance import backfill_binance_aggregate_trades
from .historical_evidence_run import run_historical_evidence_acquisition
from .historical_pipeline import HistoricalPipelineConfig
from .observed_cycle import run_observed_shadow_cycle
from .onchain_ingest import collect_and_persist_protocol_snapshot
from .portable_features import PortableFeaturePolicy
from .read_only_rpc import ReadOnlyJsonRpcClient
from .shadow_economic_summary import summarize_shadow_economics
from .shadow_economics import ShadowEconomicPolicy
from .shadow_settlement import ShadowSettlementStatus, reconcile_shadow_economic_round
from .shadow_settlement_batch import reconcile_pending_shadow_economic_rounds
from .trained_model_artifact import load_promoted_model_artifact


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


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _unit_interval_float(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be in [0, 1]")
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

    evidence = sub.add_parser(
        "historical-evidence-run",
        help="Populate one reconstructed research store from lifecycle, sparse Binance, and decision snapshots",
    )
    evidence.add_argument("--dataset-id", required=True)
    evidence.add_argument("--rpc-url", required=True)
    evidence.add_argument("--from-block", type=_non_negative_int, required=True)
    evidence.add_argument("--to-block", type=_non_negative_int, required=True)
    evidence.add_argument("--decision-lead-ns", type=_positive_int, required=True)
    evidence.add_argument("--binance-latency-ns", type=_non_negative_int, required=True)
    evidence.add_argument("--onchain-latency-ns", type=_non_negative_int, required=True)
    evidence.add_argument("--symbol", default="BNBUSDT")
    evidence.add_argument("--long-window-ns", type=_positive_int, default=30_000_000_000)
    evidence.add_argument("--short-window-ns", type=_positive_int, default=5_000_000_000)
    evidence.add_argument("--max-source-clock-skew-ns", type=_non_negative_int, default=2_000_000_000)
    evidence.add_argument("--lifecycle-chunk-size", type=_positive_int, default=5_000)
    evidence.add_argument("--binance-batch-limit", type=_positive_int, default=1_000)
    evidence.add_argument("--binance-max-batches-per-window", type=_positive_int, default=10_000)
    evidence.add_argument("--rpc-timeout-seconds", type=float, default=10.0)

    protocol = sub.add_parser("protocol-once", help="Collect one pinned Pancake + Chainlink BSC snapshot")
    protocol.add_argument("--rpc-url", required=True)
    protocol.add_argument("--rpc-timeout-seconds", type=float, default=10.0)

    cycle = sub.add_parser(
        "shadow-cycle-once",
        help="Collect observations, run promoted-model inference, and optionally record a simulated EV action",
    )
    cycle.add_argument("--rpc-url", required=True)
    cycle.add_argument("--model-artifact", type=Path, required=True)
    cycle.add_argument("--symbol", default="BNBUSDT")
    cycle.add_argument("--trade-limit", type=_positive_int, default=1000)
    cycle.add_argument("--rpc-timeout-seconds", type=float, default=10.0)
    cycle.add_argument(
        "--shadow-stake-wei",
        type=_positive_int,
        help="Enable paper EV selection using this simulated fixed stake",
    )
    cycle.add_argument("--shadow-gas-cost-wei", type=_non_negative_int, default=0)
    cycle.add_argument(
        "--shadow-claim-or-refund-gas-cost-wei",
        type=_non_negative_int,
        help=(
            "Optional decision-time paper estimate for later claim/refund gas. "
            "When omitted, winning/refund settlements remain explicitly not fully costed."
        ),
    )
    cycle.add_argument("--shadow-same-side-inflow-wei", type=_non_negative_int, default=0)
    cycle.add_argument("--shadow-opposite-side-inflow-wei", type=_non_negative_int, default=0)
    cycle.add_argument(
        "--shadow-execution-success-probability",
        type=_unit_interval_float,
        default=1.0,
    )
    cycle.add_argument(
        "--shadow-min-expected-return",
        type=_non_negative_float,
        default=0.0,
    )

    settle = sub.add_parser(
        "shadow-settle-round",
        help="Reconcile one paper economic decision against a pinned observed round state",
    )
    settle.add_argument("--rpc-url", required=True)
    settle.add_argument("--round-id", type=_non_negative_int, required=True)
    settle.add_argument("--rpc-timeout-seconds", type=float, default=10.0)

    settle_pending = sub.add_parser(
        "shadow-settle-pending",
        help="Reconcile unresolved paper decisions against one shared current BSC block",
    )
    settle_pending.add_argument("--rpc-url", required=True)
    settle_pending.add_argument("--max-rounds", type=_positive_int)
    settle_pending.add_argument("--rpc-timeout-seconds", type=float, default=10.0)

    sub.add_parser(
        "shadow-summary",
        help="Summarize settled and unresolved multi-round paper economics",
    )

    verify = sub.add_parser("verify-store", help="Verify the Event Store hash chain")
    verify.add_argument(
        "--mode",
        choices=("observed", "reconstructed"),
        default="observed",
        help="Expected persisted availability mode",
    )
    return parser


def _rpc(url: str, timeout_seconds: float) -> ReadOnlyJsonRpcClient:
    if timeout_seconds <= 0:
        raise SystemExit("--rpc-timeout-seconds must be positive")
    return ReadOnlyJsonRpcClient(url, timeout_seconds=timeout_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.store.parent.mkdir(parents=True, exist_ok=True)

    if args.command in {"historical-binance", "historical-evidence-run"}:
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

        if args.command == "historical-evidence-run":
            if args.to_block < args.from_block:
                raise SystemExit("--to-block must be >= --from-block")
            if not 1 <= args.binance_batch_limit <= 1000:
                raise SystemExit("--binance-batch-limit must be in [1, 1000]")
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
            result = run_historical_evidence_acquisition(
                store,
                config=config,
                binance_client=BinancePublicRestClient(),
                rpc_client=_rpc(args.rpc_url, args.rpc_timeout_seconds),
                from_block=args.from_block,
                to_block=args.to_block,
                symbol=args.symbol,
                lifecycle_chunk_size=args.lifecycle_chunk_size,
                binance_batch_limit=args.binance_batch_limit,
                binance_max_batches_per_window=args.binance_max_batches_per_window,
            )
            appended_trades = sum(item.events_appended for item in result.binance_results)
            print(
                f"historical-evidence dataset={result.dataset_id} "
                f"completed_rounds={result.completed_rounds} "
                f"incomplete_rounds={len(result.incomplete_epochs)} "
                f"binance_windows={len(result.binance_windows)} "
                f"binance_events={appended_trades} "
                f"decision_snapshots={len(result.decision_snapshots.points)} "
                f"examples={len(result.examples.examples)} "
                f"skipped={len(result.examples.skipped)} "
                f"events={result.store_event_count} tip={result.store_tip_hash}"
            )
            return 0

        if args.command == "protocol-once":
            client = _rpc(args.rpc_url, args.rpc_timeout_seconds)
            result = collect_and_persist_protocol_snapshot(client, store)
            snapshot = result.snapshot
            print(
                f"protocol block={snapshot.anchor.number} epoch={snapshot.current_epoch} "
                f"oracle={snapshot.oracle_address} events={len(result.stored_events)}"
            )
            return 0

        if args.command == "shadow-cycle-once":
            if not 1 <= args.trade_limit <= 1000:
                raise SystemExit("--trade-limit must be in [1, 1000]")
            economic_policy = None
            if args.shadow_stake_wei is not None:
                economic_policy = ShadowEconomicPolicy(
                    stake_wei=args.shadow_stake_wei,
                    gas_cost_wei=args.shadow_gas_cost_wei,
                    claim_or_refund_gas_cost_wei=args.shadow_claim_or_refund_gas_cost_wei,
                    same_side_inflow_wei=args.shadow_same_side_inflow_wei,
                    opposite_side_inflow_wei=args.shadow_opposite_side_inflow_wei,
                    execution_success_probability=args.shadow_execution_success_probability,
                    min_expected_return=args.shadow_min_expected_return,
                )
            artifact = load_promoted_model_artifact(args.model_artifact)
            cycle = run_observed_shadow_cycle(
                store,
                artifact,
                binance_client=BinancePublicRestClient(),
                rpc_client=_rpc(args.rpc_url, args.rpc_timeout_seconds),
                symbol=args.symbol,
                trade_limit=args.trade_limit,
                economic_policy=economic_policy,
            )
            inference = cycle.inference
            economic_text = "economic=-"
            if cycle.economic is not None:
                selected = (
                    "-"
                    if cycle.economic.selected_side is None
                    else cycle.economic.selected_side.value
                )
                best_ev = max(
                    cycle.economic.bull.expected_return_on_stake,
                    cycle.economic.bear.expected_return_on_stake,
                )
                economic_text = (
                    f"economic={cycle.economic.action.value} side={selected} "
                    f"best_expected_return={best_ev:.8f}"
                )
            print(
                f"shadow-cycle epoch={cycle.protocol.snapshot.current_epoch} "
                f"accepted={inference.accepted} blockers={','.join(inference.blockers) or '-'} "
                f"outcome={inference.predicted_outcome or '-'} {economic_text} "
                f"events={cycle.store_event_count} tip={cycle.store_tip_hash}"
            )
            return 0 if inference.accepted else 3

        if args.command == "shadow-settle-round":
            result = reconcile_shadow_economic_round(
                store,
                _rpc(args.rpc_url, args.rpc_timeout_seconds),
                round_id=args.round_id,
            )
            print(
                f"shadow-settle round={result.round_id} status={result.status.value} "
                f"resolution={'-' if result.resolution is None else result.resolution.value} "
                f"pnl_if_executed={result.pnl_if_executed_wei} "
                f"probability_adjusted_pnl={result.probability_adjusted_pnl_wei} "
                f"blockers={','.join(result.blockers) or '-'}"
            )
            return 4 if result.status is ShadowSettlementStatus.ANOMALY else 0

        if args.command == "shadow-settle-pending":
            batch = reconcile_pending_shadow_economic_rounds(
                store,
                _rpc(args.rpc_url, args.rpc_timeout_seconds),
                max_rounds=args.max_rounds,
            )
            settled = sum(
                result.status
                in {ShadowSettlementStatus.SETTLED, ShadowSettlementStatus.ALREADY_SETTLED}
                for result in batch.results
            )
            pending = sum(
                result.status is ShadowSettlementStatus.PENDING
                for result in batch.results
            )
            anomalies = sum(
                result.status is ShadowSettlementStatus.ANOMALY
                for result in batch.results
            )
            print(
                f"shadow-settle-pending attempted={len(batch.attempted_round_ids)} "
                f"settled={settled} pending={pending} anomalies={anomalies} "
                f"anchor={'-' if batch.anchor is None else batch.anchor.number}"
            )
            return 4 if anomalies else 0

        if args.command == "shadow-summary":
            summary = summarize_shadow_economics(store)
            avg_expected = (
                "-"
                if summary.average_selected_expected_return is None
                else f"{summary.average_selected_expected_return:.8f}"
            )
            print(
                f"shadow-summary decisions={summary.decision_rounds} bets={summary.bet_decisions} "
                f"abstain={summary.abstentions} settled={summary.settled_rounds} "
                f"unresolved={summary.unresolved_rounds} wins={summary.winning_bets} "
                f"losses={summary.losing_bets} ties={summary.tie_losses} refunds={summary.refunds} "
                f"conditional_net_pnl_wei={summary.conditional_net_pnl_wei} "
                f"conditional_max_drawdown_wei={summary.conditional_max_drawdown_wei} "
                f"probability_adjusted_net_pnl_wei={summary.probability_adjusted_net_pnl_wei} "
                f"probability_adjusted_max_drawdown_wei={summary.probability_adjusted_max_drawdown_wei} "
                f"average_selected_expected_return={avg_expected} "
                f"claim_or_refund_gas_fully_modeled={summary.claim_or_refund_gas_fully_modeled}"
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
