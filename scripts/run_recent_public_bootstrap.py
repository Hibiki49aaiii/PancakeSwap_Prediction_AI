from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pancake_prediction.chainlink_anchor import load_chainlink_route_anchor
from pancake_prediction.contracts import MARKETS
from pancake_prediction.recent_bootstrap import run_recent_prediction_bootstrap
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

AUTHENTICATED_RPC_ENV_ORDER = (
    "BSC_LOG_RPC_URL",
    "BSC_ARCHIVE_RPC_URL",
)

PUBLIC_RPC_TIMEOUT_S = 20.0
PUBLIC_RPC_RETRIES = 6
PUBLIC_RPC_BACKOFF_S = 1.5
PUBLIC_RPC_MIN_INTERVAL_S = 0.15


@dataclass(frozen=True, slots=True)
class RpcCandidate:
    label: str
    url: str
    authenticated: bool


def authenticated_rpc_candidates(environ: Mapping[str, str]) -> tuple[RpcCandidate, ...]:
    candidates: list[RpcCandidate] = []
    for variable in AUTHENTICATED_RPC_ENV_ORDER:
        value = environ.get(variable, "").strip()
        if value:
            candidates.append(
                RpcCandidate(
                    label=f"env:{variable}",
                    url=value,
                    authenticated=True,
                )
            )
    return tuple(candidates)


def rpc_candidates(
    *,
    require_authenticated: bool,
    environ: Mapping[str, str],
) -> tuple[RpcCandidate, ...]:
    authenticated = authenticated_rpc_candidates(environ)
    if require_authenticated:
        return authenticated
    return authenticated + tuple(
        RpcCandidate(label=endpoint, url=endpoint, authenticated=False)
        for endpoint in PUBLIC_BSC_ENDPOINTS
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
    parser.add_argument(
        "--require-authenticated-rpc",
        action="store_true",
        help=(
            "use only BSC_LOG_RPC_URL then BSC_ARCHIVE_RPC_URL; never fall back to "
            "known public endpoints"
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

    candidates = rpc_candidates(
        require_authenticated=bool(args.require_authenticated_rpc),
        environ=os.environ,
    )
    attempts: list[dict[str, object]] = []
    success: dict[str, object] | None = None

    rpc_policy: dict[str, object] = {
        "timeout_s": PUBLIC_RPC_TIMEOUT_S,
        "retries": PUBLIC_RPC_RETRIES,
        "backoff_s": PUBLIC_RPC_BACKOFF_S,
        "min_interval_s": PUBLIC_RPC_MIN_INTERVAL_S,
        "source_mode": (
            "authenticated_only"
            if bool(args.require_authenticated_rpc)
            else "authenticated_then_public"
        ),
    }

    source_requirement: dict[str, object] | None = None
    if bool(args.require_authenticated_rpc) and not candidates:
        source_requirement = {
            "classification": "AUTHENTICATED_RPC_REQUIRED",
            "accepted_env": list(AUTHENTICATED_RPC_ENV_ORDER),
        }

    for candidate in candidates:
        if args.database.exists():
            args.database.unlink()
        try:
            report = run_recent_prediction_bootstrap(
                JsonRpcClient(
                    candidate.url,
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
                "endpoint": candidate.label,
                "authenticated": candidate.authenticated,
                "report": report.as_dict(),
            }
            attempts.append(
                {
                    "endpoint": candidate.label,
                    "authenticated": candidate.authenticated,
                    "outcome": "success",
                    "error": None,
                }
            )
            break
        except Exception as exc:
            attempt: dict[str, object] = {
                "endpoint": candidate.label,
                "authenticated": candidate.authenticated,
                "outcome": "failure",
                "error": f"{type(exc).__name__}: {exc}",
            }
            detail_builder = getattr(exc, "as_dict", None)
            if callable(detail_builder):
                details = detail_builder()
                if isinstance(details, dict):
                    attempt["error_details"] = details
            attempts.append(attempt)

    report_payload = None if success is None else success.get("report")
    chainlink_collected = bool(
        isinstance(report_payload, dict)
        and report_payload.get("chainlink_collected") is True
    )
    payload = {
        "evidence_version": 7,
        "market": market,
        "requested_start_timestamp": args.start_timestamp,
        "requested_end_timestamp": args.end_timestamp,
        "success": success is not None,
        "attempts": attempts,
        "selected": success,
        "source_requirement": source_requirement,
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
