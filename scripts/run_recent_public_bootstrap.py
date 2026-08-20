from __future__ import annotations

import argparse
import json
from pathlib import Path

from pancake_prediction.contracts import MARKETS
from pancake_prediction.recent_bootstrap import run_recent_prediction_bootstrap
from pancake_prediction.rpc import JsonRpcClient

# BNB Chain documents that eth_getLogs is disabled on its public Mainnet
# dataseed endpoints and recommends third-party providers for log workloads.
# 48 Club currently advertises fast.bsc-rpc.com as its public RPC; keep the
# previously proven rpc-bsc.48.club route immediately behind it, then fall back
# through other public candidates before the official diagnostic seeds.
PUBLIC_BSC_ENDPOINTS = (
    "https://fast.bsc-rpc.com",
    "https://rpc-bsc.48.club",
    "https://bsc-pokt.nodies.app",
    "https://bsc.blockpi.network/v1/rpc/public",
    "https://bsc.drpc.org",
    "https://bnb.api.onfinality.io/public",
    "https://bsc.meowrpc.com",
    "https://bsc-mainnet.public.blastapi.io",
    "https://endpoints.omniatech.io/v1/bsc/mainnet/public",
    "https://bsc-dataseed.bnbchain.org",
    "https://bsc-dataseed-public.bnbchain.org",
    "https://bsc-dataseed.nariox.org",
    "https://bsc-dataseed.defibit.io",
    "https://bsc-dataseed.ninicoin.io",
    "https://bsc.nodereal.io",
    "https://bsc-rpc.publicnode.com",
    "https://public.1rpc.io/bnb",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=sorted(MARKETS), default="BNBUSD")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-timestamp", type=int, required=True)
    parser.add_argument("--end-timestamp", type=int, required=True)
    parser.add_argument("--confirmations", type=int, default=64)
    parser.add_argument("--chunk-size", type=int, default=2_000)
    parser.add_argument(
        "--include-chainlink",
        action="store_true",
        help=(
            "collect recent Chainlink AnswerUpdated events only after proving both "
            "the Prediction oracle proxy and its underlying aggregator were stable"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    attempts: list[dict[str, object]] = []
    success: dict[str, object] | None = None

    for endpoint in PUBLIC_BSC_ENDPOINTS:
        if args.database.exists():
            args.database.unlink()
        try:
            report = run_recent_prediction_bootstrap(
                JsonRpcClient(endpoint, timeout_s=20.0, retries=3),
                MARKETS[str(args.market)],
                args.database,
                start_timestamp=args.start_timestamp,
                end_timestamp=args.end_timestamp,
                confirmations=args.confirmations,
                chunk_size=args.chunk_size,
                include_chainlink=args.include_chainlink,
            )
            if args.include_chainlink and not report.chainlink_collected:
                raise RuntimeError(
                    "Chainlink was requested but no AnswerUpdated events were collected "
                    "from the proven underlying aggregator"
                )
            success = {
                "endpoint": endpoint,
                "report": report.as_dict(),
            }
            attempts.append(
                {
                    "endpoint": endpoint,
                    "outcome": "success",
                    "error": None,
                }
            )
            break
        except Exception as exc:
            attempts.append(
                {
                    "endpoint": endpoint,
                    "outcome": "failure",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    chainlink_collected = bool(
        success is not None
        and isinstance(success.get("report"), dict)
        and success["report"].get("chainlink_collected") is True
    )
    payload = {
        "evidence_version": 4,
        "market": str(args.market),
        "requested_start_timestamp": args.start_timestamp,
        "requested_end_timestamp": args.end_timestamp,
        "success": success is not None,
        "attempts": attempts,
        "selected": success,
        "archive_state_required": False,
        "chainlink_requested": bool(args.include_chainlink),
        "chainlink_collected": chainlink_collected,
        "signing_enabled": False,
        "live_broadcast": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if success is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
