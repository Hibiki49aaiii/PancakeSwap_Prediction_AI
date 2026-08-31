from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

from .binance_archive import (
    ArchiveVenue,
    TimestampUnit,
    inspect_archive_aggtrades,
    verify_archive_checksum,
)
from .contracts import MARKETS
from .execution_intent import ExecutionIntent, ExecutionIntentStore, ForkExecutionCoordinator
from .execution_report import build_execution_intent_report
from .historical_bootstrap import run_historical_bootstrap
from .historical_preflight import run_historical_preflight
from .oracle_history import build_active_oracle_history
from .prediction_preflight import inspect_prediction_bet_intent
from .prediction_tx import BetSide, build_prediction_bet_intent
from .replay import build_replay_snapshot
from .research_dataset import BINANCE_SYMBOL_BY_MARKET
from .rpc import JsonRpcClient, LocalForkRpcClient, RpcError
from .rpc_probe import probe_archive_state
from .shadow_campaign import ShadowCampaignPolicy, evaluate_shadow_campaign
from .shadow_chain_sync import sync_shadow_chain
from .shadow_ledger import (
    ShadowLedgerStore,
    prediction_from_payload,
    settlement_from_payload,
)
from .shadow_reconciliation import reconcile_shadow_settlements
from .shadow_runtime_health import inspect_shadow_runtime_health

PACKAGE_NAME = "pancakeswap-prediction-ai"


def package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.7.0"


def _status_payload() -> dict[str, object]:
    return {
        "package": PACKAGE_NAME,
        "version": package_version(),
        "stage": "v0.7-alpha-research",
        "live_broadcast": False,
        "signing_enabled": False,
        "fork_local_broadcast": True,
        "fork_rpc_loopback_only": True,
        "fork_prediction_preflight_required": True,
        "markets": ["BNBUSD", "BTCUSD", "ETHUSD"],
    }


def _intent_payload(intent: ExecutionIntent) -> dict[str, object]:
    return asdict(intent)


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _add_rpc_url_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rpc-url",
        default=None,
        help="BSC RPC URL; defaults to BSC_RPC_URL and is never printed",
    )


def _add_fork_rpc_url_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fork-rpc-url",
        required=True,
        help="local fork RPC URL; non-loopback endpoints are rejected and the URL is never printed",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcs-prediction",
        description="Leakage-safe PancakeSwap Prediction research tooling.",
    )
    parser.add_argument("--version", action="version", version=package_version())
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="print the current research/safety status as JSON")

    probe = subparsers.add_parser(
        "rpc-probe",
        help="verify historical BSC state access at one explicit block",
    )
    probe.add_argument("--market", choices=sorted(MARKETS), required=True)
    probe.add_argument("--block", type=int, required=True)
    _add_rpc_url_argument(probe)

    historical = subparsers.add_parser(
        "historical-preflight",
        help="discover deployment and verify archive access at the oldest required block",
    )
    historical.add_argument("--market", choices=sorted(MARKETS), required=True)
    _add_rpc_url_argument(historical)

    bootstrap = subparsers.add_parser(
        "historical-bootstrap",
        help="preflight, collect confirmed history, run quality checks, and build replay",
    )
    bootstrap.add_argument("--market", choices=sorted(MARKETS), required=True)
    bootstrap.add_argument("--db", type=Path, required=True)
    bootstrap.add_argument("--confirmations", type=int, default=64)
    bootstrap.add_argument("--from-block", type=int, default=None)
    bootstrap.add_argument("--to-block", type=int, default=None)
    bootstrap.add_argument("--chunk-size", type=int, default=2_000)
    bootstrap.add_argument("--no-chainlink", action="store_true")
    bootstrap.add_argument("--all-prediction-events", action="store_true")
    _add_rpc_url_argument(bootstrap)

    chain_sync = subparsers.add_parser(
        "shadow-chain-sync",
        help="incrementally collect anchored BSC Prediction and Chainlink events for Stage 4",
    )
    chain_sync.add_argument("--market", choices=sorted(MARKETS), required=True)
    chain_sync.add_argument("--db", type=Path, required=True)
    chain_sync.add_argument("--confirmations", type=int, default=3)
    chain_sync.add_argument("--chunk-size", type=int, default=2_000)
    chain_sync.add_argument("--reorg-lookback", type=int, default=64)
    _add_rpc_url_argument(chain_sync)

    oracle_report = subparsers.add_parser(
        "oracle-history-report",
        help="validate active Chainlink oracle history and report excluded stale/inactive feeds",
    )
    oracle_report.add_argument("--market", choices=sorted(MARKETS), required=True)
    oracle_report.add_argument("--db", type=Path, required=True)

    archive_inspect = subparsers.add_parser(
        "binance-archive-inspect",
        help="verify one official Binance aggTrades archive and print normalized provenance",
    )
    archive_inspect.add_argument("--market", choices=sorted(MARKETS), required=True)
    archive_inspect.add_argument("--archive", type=Path, required=True)
    archive_inspect.add_argument("--checksum", type=Path, required=True)
    archive_inspect.add_argument("--venue", choices=("spot", "um_futures"), required=True)
    archive_inspect.add_argument(
        "--timestamp-unit",
        choices=("auto", "milliseconds", "microseconds"),
        default="auto",
    )
    archive_inspect.add_argument("--availability-lag-ms", type=int, required=True)

    fork_prepare = subparsers.add_parser(
        "fork-prepare-account",
        help="impersonate and fund one account on a loopback local fork",
    )
    _add_fork_rpc_url_argument(fork_prepare)
    fork_prepare.add_argument("--sender", required=True)
    fork_prepare.add_argument("--balance-wei", type=int, required=True)

    fork_create = subparsers.add_parser(
        "fork-create-bet-intent",
        help="persist one Bull/Bear Prediction intent without sending a transaction",
    )
    fork_create.add_argument("--db", type=Path, required=True)
    fork_create.add_argument("--market", choices=sorted(MARKETS), required=True)
    fork_create.add_argument("--sender", required=True)
    fork_create.add_argument("--epoch", type=int, required=True)
    fork_create.add_argument("--side", choices=[side.value for side in BetSide], required=True)
    fork_create.add_argument("--stake-wei", type=int, required=True)

    fork_preflight = subparsers.add_parser(
        "fork-bet-preflight",
        help="inspect whether a durable bet intent is currently bettable on the local fork",
    )
    _add_fork_rpc_url_argument(fork_preflight)
    fork_preflight.add_argument("--db", type=Path, required=True)
    fork_preflight.add_argument("--intent-id", type=int, required=True)

    fork_submit = subparsers.add_parser(
        "fork-submit-intent",
        help="preflight and submit one Prediction bet intent to a loopback local fork only",
    )
    _add_fork_rpc_url_argument(fork_submit)
    fork_submit.add_argument("--db", type=Path, required=True)
    fork_submit.add_argument("--intent-id", type=int, required=True)
    fork_submit.add_argument("--gas", type=int, default=None)
    fork_submit.add_argument("--gas-price-wei", type=int, default=None)

    fork_reconcile = subparsers.add_parser(
        "fork-reconcile-intent",
        help="reconcile one submitted/unknown intent against a loopback local fork",
    )
    _add_fork_rpc_url_argument(fork_reconcile)
    fork_reconcile.add_argument("--db", type=Path, required=True)
    fork_reconcile.add_argument("--intent-id", type=int, required=True)
    fork_reconcile.add_argument("--confirmations", type=int, default=3)

    fork_report = subparsers.add_parser(
        "fork-intent-report",
        help="report Stage 5 resolved/unresolved durable intents without using RPC",
    )
    fork_report.add_argument("--db", type=Path, required=True)

    shadow_init = subparsers.add_parser(
        "shadow-ledger-init",
        help="initialize the append-only Stage 4 shadow ledger",
    )
    shadow_init.add_argument("--db", type=Path, required=True)

    shadow_prediction = subparsers.add_parser(
        "shadow-append-prediction",
        help="append one pre-decision research prediction JSON record to the Stage 4 ledger",
    )
    shadow_prediction.add_argument("--db", type=Path, required=True)
    shadow_prediction.add_argument("--record", type=Path, required=True)
    shadow_prediction.add_argument("--purge-rounds", type=int, default=2)

    shadow_settlement = subparsers.add_parser(
        "shadow-append-settlement",
        help="append one post-settlement result JSON record to the Stage 4 ledger",
    )
    shadow_settlement.add_argument("--db", type=Path, required=True)
    shadow_settlement.add_argument("--record", type=Path, required=True)

    shadow_audit = subparsers.add_parser(
        "shadow-ledger-audit",
        help="verify the Stage 4 hash chain and report shadow probability/economic metrics",
    )
    shadow_audit.add_argument("--db", type=Path, required=True)
    shadow_audit.add_argument("--purge-rounds", type=int, default=2)

    shadow_reconcile = subparsers.add_parser(
        "shadow-reconcile",
        help="append canonical settled outcomes for unresolved Stage 4 predictions",
    )
    shadow_reconcile.add_argument("--shadow-db", type=Path, required=True)
    shadow_reconcile.add_argument("--canonical-db", type=Path, required=True)
    shadow_reconcile.add_argument(
        "--market",
        choices=sorted(BINANCE_SYMBOL_BY_MARKET),
        required=True,
    )
    shadow_reconcile.add_argument("--purge-rounds", type=int, default=2)

    shadow_gate = subparsers.add_parser(
        "shadow-campaign-gate",
        help=(
            "evaluate Stage 4 shadow operational-readiness without treating "
            "profit as a pass condition"
        ),
    )
    shadow_gate.add_argument("--db", type=Path, required=True)
    shadow_gate.add_argument("--purge-rounds", type=int, default=2)
    shadow_gate.add_argument("--min-predictions", type=int, default=1_000)
    shadow_gate.add_argument("--min-settlements", type=int, default=900)
    shadow_gate.add_argument("--min-probability-scored", type=int, default=900)
    shadow_gate.add_argument("--min-actionable-predictions", type=int, default=200)
    shadow_gate.add_argument(
        "--min-decision-span-seconds",
        type=int,
        default=7 * 24 * 60 * 60,
    )
    shadow_gate.add_argument("--max-unresolved-ppm", type=int, default=100_000)
    shadow_gate.add_argument("--max-model-ids", type=int, default=1)
    shadow_gate.add_argument("--max-feature-set-ids", type=int, default=1)
    shadow_gate.add_argument("--allow-single-direction", action="store_true")
    shadow_gate.add_argument("--allow-missing-actionable-pnl", action="store_true")

    shadow_health = subparsers.add_parser(
        "shadow-runtime-health",
        help="check the read-only Stage 4 operational status checkpoint",
    )
    shadow_health.add_argument("--status-file", type=Path, required=True)
    shadow_health.add_argument("--max-status-age-seconds", type=float, required=True)
    shadow_health.add_argument("--max-last-success-age-seconds", type=float, default=None)
    return parser


def _rpc_url_or_error(parser: argparse.ArgumentParser, value: object) -> str:
    rpc_url = value or os.environ.get("BSC_RPC_URL")
    if not rpc_url:
        parser.error("command requires --rpc-url or BSC_RPC_URL")
    return str(rpc_url)


def _execution_store(path: str | Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(Path(path))
    store.initialize()
    return store


def _load_json_object_or_error(
    parser: argparse.ArgumentParser,
    path: Path,
    *,
    label: str,
) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"could not load {label} JSON: {exc}")
    if not isinstance(payload, dict):
        parser.error(f"{label} JSON root must be an object")
    return cast(dict[str, object], payload)


def _shadow_store(path: str | Path) -> ShadowLedgerStore:
    store = ShadowLedgerStore(Path(path))
    store.initialize()
    return store


def _prediction_preflight_or_error(
    parser: argparse.ArgumentParser,
    fork_rpc: LocalForkRpcClient,
    store: ExecutionIntentStore,
    intent_id: int,
) -> dict[str, object]:
    result = inspect_prediction_bet_intent(fork_rpc, store.get(intent_id))
    if not result.ready:
        parser.error("Prediction bet preflight failed: " + "; ".join(result.reasons))
    return result.as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        _print_json(_status_payload())
        return 0
    if args.command == "shadow-runtime-health":
        try:
            health_report = inspect_shadow_runtime_health(
                Path(args.status_file),
                max_status_age_seconds=float(args.max_status_age_seconds),
                max_last_success_age_seconds=(
                    None
                    if args.max_last_success_age_seconds is None
                    else float(args.max_last_success_age_seconds)
                ),
            )
        except ValueError as exc:
            parser.error(str(exc))
        _print_json(health_report.as_dict())
        return 0 if health_report.check_passed else 2
    if args.command == "rpc-probe":
        probe_result = probe_archive_state(
            JsonRpcClient(_rpc_url_or_error(parser, args.rpc_url)),
            MARKETS[str(args.market)],
            int(args.block),
        )
        _print_json(probe_result.as_dict())
        return 0
    if args.command == "historical-preflight":
        preflight_result = run_historical_preflight(
            JsonRpcClient(_rpc_url_or_error(parser, args.rpc_url)),
            MARKETS[str(args.market)],
        )
        _print_json(preflight_result.as_dict())
        return 0
    if args.command == "shadow-chain-sync":
        try:
            chain_report = sync_shadow_chain(
                JsonRpcClient(_rpc_url_or_error(parser, args.rpc_url)),
                MARKETS[str(args.market)],
                Path(args.db),
                confirmations=int(args.confirmations),
                chunk_size=int(args.chunk_size),
                reorg_lookback=int(args.reorg_lookback),
            )
        except (RpcError, ValueError) as exc:
            parser.error(f"shadow chain sync failed: {exc}")
        _print_json(chain_report.as_dict())
        return 0
    if args.command == "historical-bootstrap":
        bootstrap_result = run_historical_bootstrap(
            JsonRpcClient(_rpc_url_or_error(parser, args.rpc_url)),
            MARKETS[str(args.market)],
            Path(args.db),
            confirmations=int(args.confirmations),
            from_block=args.from_block,
            to_block=args.to_block,
            chunk_size=int(args.chunk_size),
            include_chainlink=not bool(args.no_chainlink),
            prediction_analytic_only=not bool(args.all_prediction_events),
        )
        _print_json(bootstrap_result.as_dict())
        return 0
    if args.command == "oracle-history-report":
        oracle_history_result = build_active_oracle_history(
            Path(args.db),
            str(args.market),
        )
        _print_json(oracle_history_result.as_dict())
        return 0
    if args.command == "binance-archive-inspect":
        availability_lag_ms = int(args.availability_lag_ms)
        if availability_lag_ms < 0:
            parser.error("--availability-lag-ms must be non-negative")
        verify_archive_checksum(Path(args.archive), Path(args.checksum))
        archive_report = inspect_archive_aggtrades(
            Path(args.archive),
            symbol=BINANCE_SYMBOL_BY_MARKET[str(args.market)],
            venue=cast(ArchiveVenue, str(args.venue)),
            timestamp_unit=cast(TimestampUnit, str(args.timestamp_unit)),
            availability_lag_ms=availability_lag_ms,
        )
        payload = archive_report.as_dict()
        payload["checksum_verified"] = True
        _print_json(payload)
        return 0
    if args.command == "fork-prepare-account":
        fork_rpc = LocalForkRpcClient(str(args.fork_rpc_url))
        balance_wei = int(args.balance_wei)
        if balance_wei < 0:
            parser.error("--balance-wei must be non-negative")
        sender = str(args.sender)
        fork_rpc.impersonate_account(sender)
        fork_rpc.set_balance(sender, balance_wei)
        _print_json({"prepared": True, "sender": sender.lower(), "balance_wei": balance_wei})
        return 0
    if args.command == "fork-create-bet-intent":
        intent = build_prediction_bet_intent(
            _execution_store(args.db),
            market=str(args.market),
            sender=str(args.sender),
            epoch=int(args.epoch),
            side=BetSide(str(args.side)),
            stake_wei=int(args.stake_wei),
        )
        _print_json(_intent_payload(intent))
        return 0
    if args.command == "fork-bet-preflight":
        store = _execution_store(args.db)
        fork_rpc = LocalForkRpcClient(str(args.fork_rpc_url))
        result = inspect_prediction_bet_intent(fork_rpc, store.get(int(args.intent_id)))
        _print_json(result.as_dict())
        return 0 if result.ready else 2
    if args.command == "fork-submit-intent":
        store = _execution_store(args.db)
        fork_rpc = LocalForkRpcClient(str(args.fork_rpc_url))
        _prediction_preflight_or_error(parser, fork_rpc, store, int(args.intent_id))
        coordinator = ForkExecutionCoordinator(store, fork_rpc)
        intent = coordinator.submit(
            int(args.intent_id),
            gas=args.gas,
            gas_price_wei=args.gas_price_wei,
        )
        _print_json(_intent_payload(intent))
        return 0
    if args.command == "fork-reconcile-intent":
        coordinator = ForkExecutionCoordinator(
            _execution_store(args.db),
            LocalForkRpcClient(str(args.fork_rpc_url)),
            confirmations=int(args.confirmations),
        )
        intent = coordinator.reconcile(int(args.intent_id))
        _print_json(_intent_payload(intent))
        return 0
    if args.command == "fork-intent-report":
        execution_report = build_execution_intent_report(Path(args.db))
        _print_json(execution_report.as_dict())
        return 0 if execution_report.gate_ready else 2
    if args.command == "shadow-ledger-init":
        shadow_store = _shadow_store(args.db)
        _print_json(shadow_store.audit().as_dict())
        return 0
    if args.command == "shadow-append-prediction":
        purge_rounds = int(args.purge_rounds)
        if purge_rounds < 0:
            parser.error("--purge-rounds must be non-negative")
        try:
            prediction_record = prediction_from_payload(
                _load_json_object_or_error(
                    parser,
                    Path(args.record),
                    label="shadow prediction",
                )
            )
            prediction_event = _shadow_store(args.db).append_prediction(
                prediction_record,
                purge_rounds=purge_rounds,
            )
        except ValueError as exc:
            parser.error(f"invalid shadow prediction: {exc}")
        _print_json(prediction_event.as_dict())
        return 0
    if args.command == "shadow-append-settlement":
        try:
            settlement_record = settlement_from_payload(
                _load_json_object_or_error(
                    parser,
                    Path(args.record),
                    label="shadow settlement",
                )
            )
            settlement_event = _shadow_store(args.db).append_settlement(
                settlement_record
            )
        except ValueError as exc:
            parser.error(f"invalid shadow settlement: {exc}")
        _print_json(settlement_event.as_dict())
        return 0
    if args.command == "shadow-ledger-audit":
        purge_rounds = int(args.purge_rounds)
        if purge_rounds < 0:
            parser.error("--purge-rounds must be non-negative")
        report = _shadow_store(args.db).audit(purge_rounds=purge_rounds)
        _print_json(report.as_dict())
        return 0 if report.integrity_ready else 2
    if args.command == "shadow-reconcile":
        purge_rounds = int(args.purge_rounds)
        if purge_rounds < 0:
            parser.error("--purge-rounds must be non-negative")
        try:
            shadow_store = _shadow_store(args.shadow_db)
            replay = build_replay_snapshot(Path(args.canonical_db), str(args.market))
            reconciliation = reconcile_shadow_settlements(shadow_store, replay)
            audit = shadow_store.audit(purge_rounds=purge_rounds)
        except ValueError as exc:
            parser.error(f"shadow reconciliation failed: {exc}")
        _print_json(
            {
                "reconciliation": reconciliation.as_dict(),
                "audit": audit.as_dict(),
            }
        )
        return 0 if audit.integrity_ready else 2
    if args.command == "shadow-campaign-gate":
        purge_rounds = int(args.purge_rounds)
        if purge_rounds < 0:
            parser.error("--purge-rounds must be non-negative")
        min_span_seconds = int(args.min_decision_span_seconds)
        if min_span_seconds < 0:
            parser.error("--min-decision-span-seconds must be non-negative")
        policy = ShadowCampaignPolicy(
            min_predictions=int(args.min_predictions),
            min_settlements=int(args.min_settlements),
            min_probability_scored=int(args.min_probability_scored),
            min_actionable_predictions=int(args.min_actionable_predictions),
            min_decision_span_ms=min_span_seconds * 1_000,
            max_unresolved_ppm=int(args.max_unresolved_ppm),
            require_both_directions=not bool(args.allow_single_direction),
            require_complete_actionable_pnl=not bool(args.allow_missing_actionable_pnl),
            max_model_ids=int(args.max_model_ids),
            max_feature_set_ids=int(args.max_feature_set_ids),
        )
        try:
            campaign = evaluate_shadow_campaign(
                _shadow_store(args.db).audit(purge_rounds=purge_rounds),
                policy,
            )
        except ValueError as exc:
            parser.error(f"invalid shadow campaign policy: {exc}")
        _print_json(campaign.as_dict())
        return 0 if campaign.gate_ready else 2
    if args.command is None:
        parser.print_help()
        return 0
    parser.error(f"unsupported command: {args.command}")
