from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .evidence_gate import Evidence
from .execution_drill import (
    make_stage5a_evidence,
    run_stage5a_execution_drill,
    write_stage5a_evidence,
)
from .fork_execution import run_stage5b_prediction_execution_probe
from .fork_harness import (
    RpcCall,
    make_stage5b_evidence,
    probe_verified_local_bsc_fork,
    write_stage5b_evidence,
)
from .local_fork_rpc import LocalForkJsonRpcClient
from .read_only_rpc import ReadOnlyJsonRpcClient
from .stage5b_evidence import make_stage5b_execution_evidence


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppai-readiness",
        description=(
            "Produce execution-readiness evidence without private keys, raw signed "
            "transactions, or mainnet transaction broadcasting"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    drill = sub.add_parser(
        "stage5a-drill",
        help="Run the local SQLite execution durability drill and write observed evidence",
    )
    drill.add_argument("--database", type=Path, required=True)
    drill.add_argument("--output", type=Path, required=True)
    drill.add_argument("--required-confirmations", type=_positive_int, default=3)

    verify = sub.add_parser(
        "stage5b-verify-fork",
        help=(
            "Verify local BSC fork provenance against an independent read-only BSC RPC; "
            "diagnostic v2 evidence does not clear Stage 6A"
        ),
    )
    verify.add_argument("--local-rpc-url", required=True)
    verify.add_argument("--upstream-rpc-url", required=True)
    verify.add_argument("--prediction-contract", required=True)
    verify.add_argument("--chainlink-contract", required=True)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--timeout-seconds", type=float, default=10.0)

    execute = sub.add_parser(
        "stage5b-execute-fork",
        help=(
            "Verify a loopback BSC fork, execute local BULL/BEAR Prediction bets, "
            "validate revert/reset paths, and write Stage 6A-eligible v3 evidence"
        ),
    )
    execute.add_argument("--local-rpc-url", required=True)
    execute.add_argument("--upstream-rpc-url", required=True)
    execute.add_argument("--prediction-contract", required=True)
    execute.add_argument("--chainlink-contract", required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--stake-wei", type=_positive_int)
    execute.add_argument("--gas-limit", type=_positive_int, default=500_000)
    execute.add_argument("--min-window-margin-seconds", type=_positive_int, default=3)
    execute.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser


def run_stage5a_command(
    *,
    database: Path,
    output: Path,
    required_confirmations: int,
) -> Evidence:
    result = run_stage5a_execution_drill(
        database,
        required_confirmations=required_confirmations,
    )
    evidence = make_stage5a_evidence(result)
    write_stage5a_evidence(evidence, output)
    return evidence


def run_stage5b_command(
    *,
    local_rpc: RpcCall,
    upstream_rpc: RpcCall,
    prediction_contract: str,
    chainlink_contract: str,
    output: Path,
) -> Evidence:
    """Write provenance-only Stage 5B v2 diagnostic evidence."""

    result = probe_verified_local_bsc_fork(
        local_rpc,
        upstream_rpc,
        prediction_contract=prediction_contract,
        chainlink_contract=chainlink_contract,
    )
    evidence = make_stage5b_evidence(result)
    write_stage5b_evidence(evidence, output)
    return evidence


def run_stage5b_execution_command(
    *,
    local_rpc: RpcCall,
    upstream_rpc: RpcCall,
    prediction_contract: str,
    chainlink_contract: str,
    output: Path,
    stake_wei: int | None = None,
    gas_limit: int = 500_000,
    min_window_margin_seconds: int = 3,
) -> Evidence:
    fork_result = probe_verified_local_bsc_fork(
        local_rpc,
        upstream_rpc,
        prediction_contract=prediction_contract,
        chainlink_contract=chainlink_contract,
    )
    execution_result = run_stage5b_prediction_execution_probe(
        local_rpc,
        fork_result=fork_result,
        stake_wei=stake_wei,
        gas_limit=gas_limit,
        min_window_margin_seconds=min_window_margin_seconds,
    )
    evidence = make_stage5b_execution_evidence(fork_result, execution_result)
    write_stage5b_evidence(evidence, output)
    return evidence


def _clients(local_url: str, upstream_url: str, timeout_seconds: float) -> tuple[LocalForkJsonRpcClient, ReadOnlyJsonRpcClient]:
    if timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    local = LocalForkJsonRpcClient(local_url, timeout_seconds=timeout_seconds)
    upstream = ReadOnlyJsonRpcClient(upstream_url, timeout_seconds=timeout_seconds)
    return local, upstream


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "stage5a-drill":
        evidence = run_stage5a_command(
            database=args.database,
            output=args.output,
            required_confirmations=args.required_confirmations,
        )
        print(
            f"stage5a-evidence sha256={evidence.artifact_sha256} "
            f"passed={evidence.passed} origin={evidence.origin.value} output={args.output}"
        )
        return 0 if evidence.passed else 2

    if args.command == "stage5b-verify-fork":
        local, upstream = _clients(
            args.local_rpc_url,
            args.upstream_rpc_url,
            args.timeout_seconds,
        )
        evidence = run_stage5b_command(
            local_rpc=local.call,
            upstream_rpc=upstream.call,
            prediction_contract=args.prediction_contract,
            chainlink_contract=args.chainlink_contract,
            output=args.output,
        )
        print(
            f"stage5b-provenance sha256={evidence.artifact_sha256} "
            f"passed={evidence.passed} upstream_verified={evidence.payload['upstream_verified']} "
            f"stage6a_eligible=False origin={evidence.origin.value} output={args.output}"
        )
        return 0 if evidence.passed else 2

    if args.command == "stage5b-execute-fork":
        local, upstream = _clients(
            args.local_rpc_url,
            args.upstream_rpc_url,
            args.timeout_seconds,
        )
        evidence = run_stage5b_execution_command(
            local_rpc=local.call,
            upstream_rpc=upstream.call,
            prediction_contract=args.prediction_contract,
            chainlink_contract=args.chainlink_contract,
            output=args.output,
            stake_wei=args.stake_wei,
            gas_limit=args.gas_limit,
            min_window_margin_seconds=args.min_window_margin_seconds,
        )
        print(
            f"stage5b-execution sha256={evidence.artifact_sha256} "
            f"passed={evidence.passed} stage6a_eligible={evidence.passed} "
            f"origin={evidence.origin.value} output={args.output}"
        )
        return 0 if evidence.passed else 2

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
