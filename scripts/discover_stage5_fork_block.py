from __future__ import annotations

import argparse
from collections.abc import Sequence

from pancake_prediction.contracts import MARKETS
from pancake_prediction.fork_discovery import discover_fork_block
from pancake_prediction.rpc import JsonRpcClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover a confirmed BSC fork block after Prediction StartRound "
            "with a strictly later timestamp."
        )
    )
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--market", choices=sorted(MARKETS), default="BNBUSD")
    parser.add_argument("--lookback-blocks", type=int, default=4_000)
    parser.add_argument("--confirmation-lag", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=500)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fork_block, _ = discover_fork_block(
        JsonRpcClient(str(args.rpc_url)),
        market=str(args.market),
        lookback_blocks=int(args.lookback_blocks),
        confirmation_lag=int(args.confirmation_lag),
        chunk_size=int(args.chunk_size),
    )
    print(fork_block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())