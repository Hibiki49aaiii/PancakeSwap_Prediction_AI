from __future__ import annotations

import argparse
from collections.abc import Sequence

from pancake_prediction.contracts import MARKETS
from pancake_prediction.fork_discovery import discover_fork_block
from pancake_prediction.rpc import JsonRpcClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover the newest confirmed bettable BSC Prediction fork block."
    )
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--market", choices=sorted(MARKETS), default="BNBUSD")
    parser.add_argument("--lookback-blocks", type=int, default=128)
    parser.add_argument("--confirmation-lag", type=int, default=4)
    parser.add_argument(
        "--min-seconds-before-lock",
        type=int,
        default=0,
        help="require this much source-block timestamp headroom before round lock",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # This RPC is read-only fork-source discovery; transaction submission remains
    # confined to LocalForkRpcClient on the later loopback Anvil process.
    point = discover_fork_block(
        JsonRpcClient(str(args.rpc_url)),
        market=str(args.market),
        lookback_blocks=int(args.lookback_blocks),
        confirmation_lag=int(args.confirmation_lag),
        min_seconds_before_lock=int(args.min_seconds_before_lock),
    )
    print(point.block_number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
