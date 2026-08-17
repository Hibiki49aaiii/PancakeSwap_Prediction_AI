from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

from .contracts import MARKETS
from .historical_preflight import run_historical_preflight
from .rpc import JsonRpcClient
from .rpc_probe import probe_archive_state

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
        "markets": ["BNBUSD", "BTCUSD", "ETHUSD"],
    }


def _add_rpc_url_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rpc-url",
        default=None,
        help="BSC RPC URL; defaults to BSC_RPC_URL and is never printed",
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
    return parser


def _rpc_url_or_error(parser: argparse.ArgumentParser, value: object) -> str:
    rpc_url = value or os.environ.get("BSC_RPC_URL")
    if not rpc_url:
        parser.error("command requires --rpc-url or BSC_RPC_URL")
    return str(rpc_url)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(_status_payload(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "rpc-probe":
        probe_result = probe_archive_state(
            JsonRpcClient(_rpc_url_or_error(parser, args.rpc_url)),
            MARKETS[str(args.market)],
            int(args.block),
        )
        print(json.dumps(probe_result.as_dict(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "historical-preflight":
        preflight_result = run_historical_preflight(
            JsonRpcClient(_rpc_url_or_error(parser, args.rpc_url)),
            MARKETS[str(args.market)],
        )
        print(json.dumps(preflight_result.as_dict(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.command is None:
        parser.print_help()
        return 0
    parser.error(f"unsupported command: {args.command}")
