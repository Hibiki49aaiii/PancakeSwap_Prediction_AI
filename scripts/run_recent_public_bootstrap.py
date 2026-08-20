from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pancake_prediction.contracts import MARKETS
from pancake_prediction.recent_bootstrap import (
    ChainlinkRouteAnchor,
    run_recent_prediction_bootstrap,
)
from pancake_prediction.rpc import JsonRpcClient

# BNB Chain documents that eth_getLogs is disabled on its public Mainnet
# dataseed endpoints and recommends third-party providers for log workloads.
# Prefer endpoints with repository evidence for confirmed recent Prediction
# logs. fast.bsc-rpc.com is intentionally excluded after repeated GitHub-runner
# HTTP 403 evidence; rpc-bsc.48.club remains the first proven recent-log route.
PUBLIC_BSC_ENDPOINTS = (
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

PUBLIC_RPC_TIMEOUT_S = 20.0
PUBLIC_RPC_RETRIES = 6
PUBLIC_RPC_BACKOFF_S = 1.5
PUBLIC_RPC_MIN_INTERVAL_S = 0.15


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def load_chainlink_route_anchor(path: Path, *, market: str) -> ChainlinkRouteAnchor:
    """Load and validate a previously persisted successful Chainlink route proof."""

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Chainlink route anchor JSON: {exc}") from exc
    payload = _object(decoded, field="anchor evidence")

    if payload.get("success") is not True:
        raise ValueError("Chainlink route anchor evidence is not successful")
    workflow_outcome = payload.get("workflow_outcome")
    if workflow_outcome not in {None, "success"}:
        raise ValueError("Chainlink route anchor workflow outcome is not successful")
    if payload.get("chainlink_collected") is not True:
        raise ValueError("Chainlink route anchor did not collect Chainlink events")
    if str(payload.get("market", "")) != market:
        raise ValueError("Chainlink route anchor market does not match requested market")

    selected = _object(payload.get("selected"), field="anchor selected")
    report = _object(selected.get("report"), field="anchor selected.report")
    if report.get("authoritative_prediction_events") is not True:
        raise ValueError("Chainlink route anchor lacks authoritative Prediction events")
    if report.get("chainlink_collected") is not True:
        raise ValueError("Chainlink route anchor report did not collect Chainlink events")

    proof = _object(
        report.get("oracle_stability_proof"),
        field="anchor selected.report.oracle_stability_proof",
    )
    if int(proof.get("new_oracle_events", -1)) != 0:
        raise ValueError("Chainlink route anchor contains a Prediction oracle change")
    if int(proof.get("aggregator_confirmed_events", -1)) != 0:
        raise ValueError("Chainlink route anchor contains a Chainlink aggregator change")

    anchor_block = int(proof.get("from_block", 0))
    proof_through_block = int(proof.get("through_block", 0))
    if anchor_block <= 0 or proof_through_block < anchor_block:
        raise ValueError("Chainlink route anchor has an invalid proven block range")
    oracle_proxy = str(proof.get("oracle", "")).lower()
    chainlink_aggregator = str(proof.get("chainlink_aggregator", "")).lower()

    collection = _object(report.get("collection"), field="anchor selected.report.collection")
    oracle_addresses = collection.get("oracle_addresses")
    chainlink_addresses = collection.get("chainlink_event_addresses")
    if oracle_addresses != [oracle_proxy]:
        raise ValueError("Chainlink route anchor proxy disagrees with collection evidence")
    if chainlink_addresses != [chainlink_aggregator]:
        raise ValueError("Chainlink route anchor aggregator disagrees with collection evidence")
    if int(collection.get("chainlink_events_inserted", 0)) <= 0:
        raise ValueError("Chainlink route anchor contains no AnswerUpdated events")

    return ChainlinkRouteAnchor(
        oracle_proxy=oracle_proxy,
        chainlink_aggregator=chainlink_aggregator,
        anchor_block=anchor_block,
        evidence_sha256=digest,
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
    parser.add_argument(
        "--chainlink-route-anchor-evidence",
        type=Path,
        help=(
            "persisted successful later-window Chainlink evidence whose proven route "
            "will be extended backward using stateless change-event scans"
        ),
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    market = str(args.market)
    anchor_path = cast(Path | None, args.chainlink_route_anchor_evidence)
    if anchor_path is not None and not bool(args.include_chainlink):
        parser.error("--chainlink-route-anchor-evidence requires --include-chainlink")

    try:
        route_anchor = (
            None
            if anchor_path is None
            else load_chainlink_route_anchor(anchor_path, market=market)
        )
    except (OSError, ValueError) as exc:
        parser.error(f"invalid Chainlink route anchor evidence: {exc}")

    attempts: list[dict[str, object]] = []
    success: dict[str, object] | None = None

    rpc_policy: dict[str, object] = {
        "timeout_s": PUBLIC_RPC_TIMEOUT_S,
        "retries": PUBLIC_RPC_RETRIES,
        "backoff_s": PUBLIC_RPC_BACKOFF_S,
        "min_interval_s": PUBLIC_RPC_MIN_INTERVAL_S,
    }

    for endpoint in PUBLIC_BSC_ENDPOINTS:
        if args.database.exists():
            args.database.unlink()
        try:
            report = run_recent_prediction_bootstrap(
                JsonRpcClient(
                    endpoint,
                    timeout_s=PUBLIC_RPC_TIMEOUT_S,
                    retries=PUBLIC_RPC_RETRIES,
                    backoff_s=PUBLIC_RPC_BACKOFF_S,
                    min_interval_s=PUBLIC_RPC_MIN_INTERVAL_S,
                ),
                MARKETS[market],
                args.database,
                start_timestamp=args.start_timestamp,
                end_timestamp=args.end_timestamp,
                confirmations=args.confirmations,
                chunk_size=args.chunk_size,
                include_chainlink=args.include_chainlink,
                chainlink_route_anchor=route_anchor,
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
        "evidence_version": 6,
        "market": market,
        "requested_start_timestamp": args.start_timestamp,
        "requested_end_timestamp": args.end_timestamp,
        "success": success is not None,
        "attempts": attempts,
        "selected": success,
        "rpc_policy": rpc_policy,
        "archive_state_required": False,
        "chainlink_requested": bool(args.include_chainlink),
        "chainlink_collected": chainlink_collected,
        "chainlink_route_anchor": None if route_anchor is None else route_anchor.as_dict(),
        "route_proof_mode": (
            "none"
            if not bool(args.include_chainlink)
            else (
                "window_end_state"
                if route_anchor is None
                else "persisted_evidence_anchor_backward_change_scan"
            )
        ),
        "signing_enabled": False,
        "live_broadcast": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if success is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())