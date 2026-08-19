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
from .fork_harness import (
    RpcCall,
    make_stage5b_evidence,
    probe_verified_local_bsc_fork,
    write_stage5b_evidence,
)
from .local_fork_rpc import LocalForkJsonRpcClient
from .read_only_rpc import ReadOnlyJsonRpcClient


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ppai-readiness",
        description="Produce schema-bound execution-readiness evidence without signing or broadcasting",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    drill = sub.add_parser(
        "stage5a-drill",
        help="Run the local SQLite execution durability drill and write observed evidence",
    )
    drill.add_argument("--database", type=Path, required=True)
    drill.add_argument("--output", type=Path, required=True)
    drill.add_argument("--required-confirmations", type=_positive_int, default=3)

    fork = sub.add_parser(
        "stage5b-verify-fork",
        help="Verify a local BSC fork against an independent read-only BSC RPC and write evidence",
    )
    fork.add_argument("--local-rpc-url", required=True)
    fork.add_argument("--upstream-rpc-url", required=True)
    fork.add_argument("--prediction-contract", required=True)
    fork.add_argument("--chainlink-contract", required=True)
    fork.add_argument("--output", type=Path, required=True)
    fork.add_argument("--timeout-seconds", type=float, default=10.0)
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
    result = probe_verified_local_bsc_fork(
        local_rpc,
        upstream_rpc,
        prediction_contract=prediction_contract,
        chainlink_contract=chainlink_contract,
    )
    evidence = make_stage5b_evidence(result)
    write_stage5b_evidence(evidence, output)
    return evidence


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
        if args.timeout_seconds <= 0:
            raise SystemExit("--timeout-seconds must be positive")
        local = LocalForkJsonRpcClient(
            args.local_rpc_url,
            timeout_seconds=args.timeout_seconds,
        )
        upstream = ReadOnlyJsonRpcClient(
            args.upstream_rpc_url,
            timeout_seconds=args.timeout_seconds,
        )
        evidence = run_stage5b_command(
            local_rpc=local.call,
            upstream_rpc=upstream.call,
            prediction_contract=args.prediction_contract,
            chainlink_contract=args.chainlink_contract,
            output=args.output,
        )
        print(
            f"stage5b-evidence sha256={evidence.artifact_sha256} "
            f"passed={evidence.passed} upstream_verified={evidence.payload['upstream_verified']} "
            f"origin={evidence.origin.value} output={args.output}"
        )
        return 0 if evidence.passed else 2

    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
