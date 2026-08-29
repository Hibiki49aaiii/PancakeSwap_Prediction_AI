from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import cast

from .binance_archive import TimestampUnit
from .binance_live import (
    BinanceLiveError,
    BinanceLiveSourceIntegrityError,
    BinancePublicHttpClient,
)
from .binance_live_lock import (
    BinanceLiveLineageLockError,
    BinanceLiveLineageProcessLock,
)
from .clickhouse import ClickHouseError, ClickHouseHttpClient
from .contracts import MARKETS
from .rpc import JsonRpcClient, RpcError
from .shadow_campaign import build_shadow_campaign_evidence
from .shadow_chain_sync import ShadowChainSourceIntegrityError
from .shadow_inference import ShadowInferenceConfig
from .shadow_preflight import (
    ShadowRuntimePreflightReport,
    run_shadow_runtime_preflight,
)
from .shadow_runtime import (
    ShadowRuntimeConfig,
    ShadowRuntimeCycleReport,
    run_shadow_runtime_cycle,
)
from .shadow_runtime_lock import (
    ShadowRuntimeLockError,
    ShadowRuntimeProcessLock,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcs-shadow-runtime",
        description=(
            "Prospective Stage 4 collector/inference runtime. "
            "Never signs or broadcasts a transaction."
        ),
    )
    parser.add_argument("--market", choices=sorted(MARKETS), required=True)
    parser.add_argument("--canonical-db", type=Path, required=True)
    parser.add_argument("--shadow-db", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-consecutive-cycle-errors", type=int, default=5)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-output", type=Path)
    parser.add_argument("--evidence-output", type=Path)
    parser.add_argument("--campaign-evidence-output", type=Path)
    parser.add_argument("--campaign-last-success-output", type=Path)

    parser.add_argument("--chain-confirmations", type=int, default=3)
    parser.add_argument("--chain-chunk-size", type=int, default=2_000)
    parser.add_argument("--chain-reorg-lookback", type=int, default=64)

    parser.add_argument(
        "--spot-timestamp-unit",
        choices=("auto", "milliseconds", "microseconds"),
        default="auto",
    )
    parser.add_argument("--spot-availability-lag-ms", type=int, default=250)
    parser.add_argument(
        "--perp-timestamp-unit",
        choices=("auto", "milliseconds", "microseconds"),
        default="milliseconds",
    )
    parser.add_argument("--perp-availability-lag-ms", type=int, default=250)
    parser.add_argument("--no-perp", action="store_true")
    parser.add_argument("--binance-bootstrap-window-ms", type=int, default=120_000)
    parser.add_argument("--binance-batch-size", type=int, default=5_000)
    parser.add_argument("--binance-max-pages", type=int, default=100)

    parser.add_argument("--dataset-chunk-span-ms", type=int, default=3_600_000)
    parser.add_argument("--flow-lookback-ms", type=int, default=60_000)
    parser.add_argument("--max-spot-age-ms", type=int, default=5_000)
    parser.add_argument("--max-perp-age-ms", type=int, default=5_000)
    parser.add_argument("--max-chainlink-age-ms", type=int, default=300_000)
    parser.add_argument("--chainlink-availability-lag-ms", type=int, default=1_000)
    parser.add_argument("--oracle-history-updates", type=int, default=512)
    parser.add_argument("--oracle-hazard-horizon-ms", type=int, default=5_000)
    parser.add_argument("--oracle-hazard-min-intervals", type=int, default=8)

    parser.add_argument("--stake-wei", type=int, required=True)
    parser.add_argument("--bet-gas-wei", type=int, required=True)
    parser.add_argument("--claim-gas-wei", type=int, required=True)
    parser.add_argument("--inclusion-latency-seconds", type=int, required=True)
    parser.add_argument("--min-expected-value-wei", type=int, default=0)
    parser.add_argument("--decision-lead-seconds", type=int, default=20)
    parser.add_argument("--initial-interval-seconds", type=int, default=300)
    parser.add_argument("--initial-treasury-fee-bps", type=int, default=300)
    parser.add_argument("--initial-buffer-seconds", type=int, default=30)
    parser.add_argument("--min-train-rounds", type=int, default=300)
    parser.add_argument("--calibration-rounds", type=int, default=60)
    parser.add_argument("--calibration-bins", type=int, default=10)
    parser.add_argument("--calibration-shrinkage", type=int, default=20)
    parser.add_argument("--purge-rounds", type=int, default=2)
    parser.add_argument("--pool-min-train-rounds", type=int, default=150)
    parser.add_argument("--pool-window-rounds", type=int, default=400)
    return parser


def _runtime_config(args: argparse.Namespace) -> ShadowRuntimeConfig:
    inference = ShadowInferenceConfig(
        min_train_rounds=int(args.min_train_rounds),
        calibration_rounds=int(args.calibration_rounds),
        calibration_bins=int(args.calibration_bins),
        calibration_shrinkage=int(args.calibration_shrinkage),
        purge_rounds=int(args.purge_rounds),
        pool_min_train_rounds=int(args.pool_min_train_rounds),
        pool_window_rounds=int(args.pool_window_rounds),
        stake_wei=int(args.stake_wei),
        bet_gas_wei=int(args.bet_gas_wei),
        claim_gas_wei=int(args.claim_gas_wei),
        inclusion_latency_seconds=int(args.inclusion_latency_seconds),
        min_expected_value_wei=int(args.min_expected_value_wei),
        decision_lead_seconds=int(args.decision_lead_seconds),
        initial_interval_seconds=int(args.initial_interval_seconds),
        initial_treasury_fee_bps=int(args.initial_treasury_fee_bps),
        initial_buffer_seconds=int(args.initial_buffer_seconds),
    )
    return ShadowRuntimeConfig(
        chain_confirmations=int(args.chain_confirmations),
        chain_chunk_size=int(args.chain_chunk_size),
        chain_reorg_lookback=int(args.chain_reorg_lookback),
        spot_timestamp_unit=cast(TimestampUnit, str(args.spot_timestamp_unit)),
        spot_availability_lag_ms=int(args.spot_availability_lag_ms),
        perp_timestamp_unit=cast(TimestampUnit, str(args.perp_timestamp_unit)),
        perp_availability_lag_ms=int(args.perp_availability_lag_ms),
        include_perp=not bool(args.no_perp),
        binance_bootstrap_window_ms=int(args.binance_bootstrap_window_ms),
        binance_batch_size=int(args.binance_batch_size),
        binance_max_pages=int(args.binance_max_pages),
        dataset_chunk_span_ms=int(args.dataset_chunk_span_ms),
        flow_lookback_ms=int(args.flow_lookback_ms),
        max_spot_age_ms=int(args.max_spot_age_ms),
        max_perp_age_ms=int(args.max_perp_age_ms),
        max_chainlink_age_ms=int(args.max_chainlink_age_ms),
        chainlink_availability_lag_ms=int(args.chainlink_availability_lag_ms),
        oracle_history_updates=int(args.oracle_history_updates),
        oracle_hazard_horizon_ms=int(args.oracle_hazard_horizon_ms),
        oracle_hazard_min_intervals=int(args.oracle_hazard_min_intervals),
        inference=inference,
    )


def _bsc_rpc_url(parser: argparse.ArgumentParser) -> str:
    value = os.environ.get("BSC_RPC_URL")
    if not value:
        parser.error("pcs-shadow-runtime requires BSC_RPC_URL")
    return value


def _clickhouse_client(parser: argparse.ArgumentParser) -> ClickHouseHttpClient:
    endpoint = os.environ.get("CLICKHOUSE_URL")
    if not endpoint:
        parser.error("pcs-shadow-runtime requires CLICKHOUSE_URL")
    return ClickHouseHttpClient(
        endpoint,
        database=os.environ.get("CLICKHOUSE_DATABASE", "default"),
        username=os.environ.get("CLICKHOUSE_USER"),
        password=os.environ.get("CLICKHOUSE_PASSWORD"),
    )


def _render(report: ShadowRuntimeCycleReport) -> str:
    return json.dumps(
        report.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )


def _render_preflight(report: ShadowRuntimePreflightReport) -> str:
    return json.dumps(
        report.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
    )


def _write_evidence(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(rendered + "\n", encoding="utf-8")
    temporary.replace(path)


def _render_payload(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_evidence_output_paths(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    seen: dict[Path, str] = {}
    for label, raw_path in (
        ("--preflight-output", args.preflight_output),
        ("--evidence-output", args.evidence_output),
        ("--campaign-evidence-output", args.campaign_evidence_output),
        ("--campaign-last-success-output", args.campaign_last_success_output),
    ):
        if raw_path is None:
            continue
        path = Path(raw_path).resolve()
        previous = seen.get(path)
        if previous is not None:
            parser.error(f"{label} must differ from {previous}")
        seen[path] = label


def _checkpoint_campaign_evidence(
    args: argparse.Namespace,
    report: ShadowRuntimeCycleReport,
) -> None:
    latest_path = args.campaign_evidence_output
    last_success_path = args.campaign_last_success_output
    if latest_path is None and last_success_path is None:
        return

    ledger_path = Path(args.shadow_db)
    if latest_path is not None:
        latest = build_shadow_campaign_evidence(
            ledger_path,
            report.campaign,
            evidence_role="latest_attempt",
        )
        _write_evidence(Path(latest_path), _render_payload(latest))

    if last_success_path is not None and report.campaign.gate_ready:
        last_success = build_shadow_campaign_evidence(
            ledger_path,
            report.campaign,
            evidence_role="last_success",
        )
        _write_evidence(Path(last_success_path), _render_payload(last_success))


def _run_once(
    args: argparse.Namespace,
    *,
    rpc: JsonRpcClient,
    clickhouse: ClickHouseHttpClient,
    binance: BinancePublicHttpClient,
    config: ShadowRuntimeConfig,
) -> ShadowRuntimeCycleReport:
    return run_shadow_runtime_cycle(
        rpc,
        clickhouse,
        binance,
        MARKETS[str(args.market)],
        Path(args.canonical_db),
        Path(args.shadow_db),
        config=config,
    )


def _cycle_error_retry_payload(
    exc: Exception,
    *,
    consecutive_cycle_errors: int,
    max_consecutive_cycle_errors: int,
    retry_after_seconds: float,
) -> dict[str, object]:
    return {
        "status": "cycle_error_retry",
        "error_type": type(exc).__name__,
        "consecutive_cycle_errors": consecutive_cycle_errors,
        "max_consecutive_cycle_errors": max_consecutive_cycle_errors,
        "retry_after_seconds": retry_after_seconds,
        "signing_enabled": False,
        "live_broadcast": False,
        "funded_execution": False,
        "profitability_gate_eligible": False,
    }

def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    poll_seconds = float(args.poll_seconds)
    if poll_seconds < 1.0:
        parser.error("--poll-seconds must be at least 1.0")
    max_consecutive_cycle_errors = int(args.max_consecutive_cycle_errors)
    if max_consecutive_cycle_errors < 1:
        parser.error("--max-consecutive-cycle-errors must be at least 1")

    _validate_evidence_output_paths(parser, args)
    if bool(args.preflight_only):
        if any(
            value is not None
            for value in (
                args.evidence_output,
                args.campaign_evidence_output,
                args.campaign_last_success_output,
            )
        ):
            parser.error(
                "--preflight-only cannot be combined with cycle/campaign Evidence outputs"
            )
    elif args.preflight_output is not None:
        parser.error("--preflight-output requires --preflight-only")

    config = _runtime_config(args)
    try:
        config.validate()
    except ValueError as exc:
        parser.error(f"invalid Stage 4 runtime configuration: {exc}")

    rpc = JsonRpcClient(_bsc_rpc_url(parser))
    clickhouse = _clickhouse_client(parser)
    binance = BinancePublicHttpClient()

    if bool(args.preflight_only):
        preflight_report = run_shadow_runtime_preflight(
            rpc,
            clickhouse,
            binance,
            MARKETS[str(args.market)],
            Path(args.canonical_db),
            Path(args.shadow_db),
            config=config,
        )
        rendered = _render_preflight(preflight_report)
        print(rendered, flush=True)
        if args.preflight_output is not None:
            _write_evidence(Path(args.preflight_output), rendered)
        return 0 if preflight_report.ready else 2

    try:
        with ShadowRuntimeProcessLock(Path(args.shadow_db)), ExitStack() as lineage_locks:
            lineage_locks.enter_context(
                BinanceLiveLineageProcessLock(
                    clickhouse,
                    market=str(args.market),
                    venue="spot",
                    timestamp_unit=config.spot_timestamp_unit,
                    availability_lag_ms=config.spot_availability_lag_ms,
                )
            )
            if config.include_perp:
                lineage_locks.enter_context(
                    BinanceLiveLineageProcessLock(
                        clickhouse,
                        market=str(args.market),
                        venue="um_futures",
                        timestamp_unit=config.perp_timestamp_unit,
                        availability_lag_ms=config.perp_availability_lag_ms,
                    )
                )
            consecutive_cycle_errors = 0
            while True:
                try:
                    cycle_report = _run_once(
                        args,
                        rpc=rpc,
                        clickhouse=clickhouse,
                        binance=binance,
                        config=config,
                    )
                except (
                    BinanceLiveSourceIntegrityError,
                    ShadowChainSourceIntegrityError,
                    ValueError,
                ) as exc:
                    parser.error(
                        "Stage 4 runtime cycle failed with "
                        f"{type(exc).__name__}"
                    )
                except (BinanceLiveError, ClickHouseError, RpcError) as exc:
                    if bool(args.once):
                        parser.error(
                            "Stage 4 runtime cycle failed with "
                            f"{type(exc).__name__}"
                        )
                    consecutive_cycle_errors += 1
                    if consecutive_cycle_errors >= max_consecutive_cycle_errors:
                        parser.error(
                            "Stage 4 runtime reached the maximum consecutive "
                            f"cycle errors ({max_consecutive_cycle_errors}); "
                            f"last error type: {type(exc).__name__}"
                        )
                    print(
                        _render_payload(
                            _cycle_error_retry_payload(
                                exc,
                                consecutive_cycle_errors=consecutive_cycle_errors,
                                max_consecutive_cycle_errors=(
                                    max_consecutive_cycle_errors
                                ),
                                retry_after_seconds=poll_seconds,
                            )
                        ),
                        flush=True,
                    )
                    try:
                        time.sleep(poll_seconds)
                    except KeyboardInterrupt:
                        return 0
                    continue

                consecutive_cycle_errors = 0
                rendered = _render(cycle_report)
                print(rendered, flush=True)
                if args.evidence_output is not None:
                    _write_evidence(Path(args.evidence_output), rendered)
                try:
                    _checkpoint_campaign_evidence(args, cycle_report)
                except (OSError, ValueError) as exc:
                    parser.error(
                        f"Stage 4 campaign evidence checkpoint failed: {exc}"
                    )
                if bool(args.once):
                    return 0
                try:
                    time.sleep(poll_seconds)
                except KeyboardInterrupt:
                    return 0
    except (ShadowRuntimeLockError, BinanceLiveLineageLockError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
