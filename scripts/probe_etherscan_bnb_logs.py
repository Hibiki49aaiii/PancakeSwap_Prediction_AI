from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pancake_prediction.abi import PREDICTION_EVENTS
from pancake_prediction.contracts import MARKETS
from pancake_prediction.explorer_logs import EtherscanV2LogsClient, ExplorerApiError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.environ.get("ETHERSCAN_API_KEY", "").strip()
    payload: dict[str, object] = {
        "evidence_version": 1,
        "provider": "etherscan-v2",
        "chain_id": 56,
        "configured": bool(api_key),
        "bnb_free_tier_available": False,
        "logs_access_ready": False,
        "credential_persisted": False,
        "signing_enabled": False,
        "live_broadcast": False,
    }
    exit_code = 0
    if not api_key:
        payload["reason"] = "ETHERSCAN_API_KEY_not_configured"
    else:
        market = MARKETS["BNBUSD"]
        if market.deployment_block_hint is None:
            raise SystemExit("BNBUSD deployment block hint is required")
        start_round = next(spec for spec in PREDICTION_EVENTS if spec.name == "StartRound")
        client = EtherscanV2LogsClient(api_key, chain_id=56, page_size=1, retries=1)
        try:
            logs = client.get_logs(
                market.address,
                market.deployment_block_hint,
                market.deployment_block_hint,
                topic0s=(start_round.topic0,),
            )
            payload["logs_access_ready"] = True
            payload["sample_log_count"] = len(logs)
            payload["explorer_manifest"] = client.evidence_manifest()
        except ExplorerApiError as exc:
            payload["reason"] = str(exc)
            exit_code = 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if api_key and api_key in rendered:
        raise RuntimeError("credential leak detected in preflight evidence")
    args.output.write_text(rendered, encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
