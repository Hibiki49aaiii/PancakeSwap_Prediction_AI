from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pancake_prediction.contracts import MARKETS
from pancake_prediction.historical_preflight import run_historical_preflight
from pancake_prediction.rpc import JsonRpcClient


def _redact(value: str, secret: str) -> str:
    return value.replace(secret, "<redacted>") if secret else value


def build_report(market: str) -> dict[str, object]:
    rpc_url = os.environ.get("BSC_ARCHIVE_RPC_URL", "").strip()
    if not rpc_url:
        return {
            "evidence_version": 1,
            "market": market,
            "configured": False,
            "archive_ready": False,
            "reason": "BSC_ARCHIVE_RPC_URL secret is not configured",
        }

    try:
        result = run_historical_preflight(JsonRpcClient(rpc_url), MARKETS[market])
        return {
            "evidence_version": 1,
            "market": market,
            "configured": True,
            "archive_ready": True,
            "preflight": result.as_dict(),
            "error": None,
        }
    except Exception as exc:
        return {
            "evidence_version": 1,
            "market": market,
            "configured": True,
            "archive_ready": False,
            "preflight": None,
            "error": _redact(f"{type(exc).__name__}: {exc}", rpc_url),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=sorted(MARKETS), default="BNBUSD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = build_report(str(args.market))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["archive_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
