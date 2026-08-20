from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from pancake_prediction.contracts import MARKETS
from pancake_prediction.rpc import JsonRpcClient
from scripts.run_recent_public_bootstrap import PUBLIC_BSC_ENDPOINTS

ADDITIONAL_PUBLIC_BSC_ENDPOINTS = (
    "https://bnb.api.onfinality.io/public",
    "https://bsc.drpc.org",
    "https://bsc-mainnet.public.blastapi.io",
)
PUBLIC_BLOCK_RECEIPT_ENDPOINTS = tuple(
    dict.fromkeys((*PUBLIC_BSC_ENDPOINTS, *ADDITIONAL_PUBLIC_BSC_ENDPOINTS))
)


def _receipt_summary(result: object) -> dict[str, object]:
    if not isinstance(result, list):
        raise ValueError("eth_getBlockReceipts did not return a list")
    receipts = cast(list[object], result)
    logs = 0
    for item in receipts:
        if not isinstance(item, dict):
            raise ValueError("eth_getBlockReceipts contains a malformed receipt")
        raw_logs = cast(dict[str, Any], item).get("logs")
        if not isinstance(raw_logs, list):
            raise ValueError("block receipt is missing logs")
        logs += len(raw_logs)
    return {"receipt_count": len(receipts), "log_count": logs}


def _probe_endpoint(endpoint: str) -> dict[str, object]:
    client = JsonRpcClient(endpoint, timeout_s=20.0, retries=2)
    chain_id = client.chain_id()
    head = client.block_number()
    recent_block = max(0, head - 64)
    historical_block = MARKETS["BNBUSD"].deployment_block_hint
    if historical_block is None:
        raise ValueError("BNBUSD deployment block hint is unavailable")

    recent_header = client.block(recent_block)
    historical_header = client.block(historical_block)
    recent_bloom = recent_header.get("logsBloom")
    historical_bloom = historical_header.get("logsBloom")
    if not isinstance(recent_bloom, str) or not recent_bloom.startswith("0x"):
        raise ValueError("recent block header is missing logsBloom")
    if not isinstance(historical_bloom, str) or not historical_bloom.startswith("0x"):
        raise ValueError("historical block header is missing logsBloom")

    recent_receipts = client.call("eth_getBlockReceipts", [hex(recent_block)])
    historical_receipts = client.call("eth_getBlockReceipts", [hex(historical_block)])
    return {
        "chain_id": chain_id,
        "head_block": head,
        "recent_block": recent_block,
        "recent_logs_bloom_bytes": len(recent_bloom.removeprefix("0x")) // 2,
        "recent_receipts": _receipt_summary(recent_receipts),
        "historical_block": historical_block,
        "historical_logs_bloom_bytes": len(historical_bloom.removeprefix("0x")) // 2,
        "historical_receipts": _receipt_summary(historical_receipts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    attempts: list[dict[str, object]] = []
    selected: dict[str, object] | None = None
    for endpoint in PUBLIC_BLOCK_RECEIPT_ENDPOINTS:
        try:
            result = _probe_endpoint(endpoint)
            attempts.append({"endpoint": endpoint, "outcome": "success", "result": result})
            selected = {"endpoint": endpoint, "result": result}
            break
        except Exception as exc:
            attempts.append(
                {
                    "endpoint": endpoint,
                    "outcome": "failure",
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )

    payload = {
        "evidence_version": 1,
        "method": "eth_getBlockReceipts",
        "public_rpc_only": True,
        "eth_get_logs_used": False,
        "archive_state_required": False,
        "success": selected is not None,
        "selected": selected,
        "attempts": attempts,
        "signing_enabled": False,
        "live_broadcast": False,
        "profitability_gate_eligible": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if selected is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
