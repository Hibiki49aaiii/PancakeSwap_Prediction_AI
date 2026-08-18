from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pancake_prediction.contracts import MARKETS
from pancake_prediction.explorer_bootstrap import run_explorer_historical_bootstrap
from pancake_prediction.explorer_logs import EtherscanV2LogsClient
from pancake_prediction.rpc import JsonRpcClient

DEFAULT_CANONICAL_RPC = "https://bsc-dataseed-public.bnbchain.org"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=sorted(MARKETS), default="BNBUSD")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    parser.add_argument("--chunk-size", type=int, default=2_000)
    parser.add_argument("--prediction-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("ETHERSCAN_API_KEY is required")
    rpc_url = os.environ.get("BSC_CANONICAL_RPC_URL", DEFAULT_CANONICAL_RPC).strip()
    if not rpc_url:
        raise SystemExit("BSC_CANONICAL_RPC_URL cannot be empty")

    explorer = EtherscanV2LogsClient(api_key, chain_id=56)
    report = run_explorer_historical_bootstrap(
        JsonRpcClient(rpc_url, timeout_s=20.0, retries=3),
        explorer,
        MARKETS[str(args.market)],
        args.database,
        from_block=args.from_block,
        to_block=args.to_block,
        include_chainlink=not args.prediction_only,
        chunk_size=args.chunk_size,
    )
    payload = report.as_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
