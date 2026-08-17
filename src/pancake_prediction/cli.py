from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcs-prediction",
        description="Leakage-safe PancakeSwap Prediction research tooling.",
    )
    parser.add_argument("--version", action="version", version=package_version())
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("status", help="print the current research/safety status as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(_status_payload(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.command is None:
        parser.print_help()
        return 0
    parser.error(f"unsupported command: {args.command}")
