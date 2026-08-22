from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

AUTHENTICATED_RPC_ENV_ORDER = (
    "BSC_LOG_RPC_URL",
    "BSC_ARCHIVE_RPC_URL",
)


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


def recent_rpc_candidates(
    *,
    require_authenticated: bool,
    environ: Mapping[str, str],
    public_endpoints: Sequence[str],
) -> tuple[RpcCandidate, ...]:
    authenticated = authenticated_rpc_candidates(environ)
    if require_authenticated:
        return authenticated
    return authenticated + tuple(
        RpcCandidate(label=endpoint, url=endpoint, authenticated=False)
        for endpoint in public_endpoints
    )


def authenticated_rpc_requirement(
    candidates: Sequence[RpcCandidate],
) -> dict[str, object] | None:
    if candidates:
        return None
    return {
        "classification": "AUTHENTICATED_RPC_REQUIRED",
        "accepted_env": list(AUTHENTICATED_RPC_ENV_ORDER),
    }
