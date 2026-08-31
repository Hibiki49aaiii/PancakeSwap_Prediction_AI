from __future__ import annotations

from pancake_prediction.recent_rpc_selection import (
    AUTHENTICATED_RPC_ENV_ORDER,
    authenticated_rpc_candidates,
    authenticated_rpc_requirement,
    recent_rpc_candidates,
)

PUBLIC_ENDPOINTS = (
    "https://public-one.example",
    "https://public-two.example",
)


def test_authenticated_rpc_candidates_prefer_log_then_archive_and_redact_urls() -> None:
    environment = {
        "BSC_LOG_RPC_URL": "https://provider.example/log-secret-key",
        "BSC_ARCHIVE_RPC_URL": "https://provider.example/archive-secret-key",
    }

    candidates = authenticated_rpc_candidates(environment)

    assert [candidate.label for candidate in candidates] == [
        "env:BSC_LOG_RPC_URL",
        "env:BSC_ARCHIVE_RPC_URL",
    ]
    assert [candidate.url for candidate in candidates] == [
        environment["BSC_LOG_RPC_URL"],
        environment["BSC_ARCHIVE_RPC_URL"],
    ]
    assert all(candidate.authenticated for candidate in candidates)
    assert all("secret-key" not in candidate.label for candidate in candidates)


def test_default_rpc_candidates_keep_public_fallback_after_authenticated_sources() -> None:
    environment = {"BSC_LOG_RPC_URL": "https://provider.example/secret"}

    candidates = recent_rpc_candidates(
        require_authenticated=False,
        environ=environment,
        public_endpoints=PUBLIC_ENDPOINTS,
    )

    assert candidates[0].label == "env:BSC_LOG_RPC_URL"
    assert candidates[0].authenticated is True
    assert tuple(candidate.label for candidate in candidates[1:]) == PUBLIC_ENDPOINTS
    assert all(candidate.authenticated is False for candidate in candidates[1:])


def test_authenticated_only_mode_never_falls_back_to_public_endpoints() -> None:
    candidates = recent_rpc_candidates(
        require_authenticated=True,
        environ={},
        public_endpoints=PUBLIC_ENDPOINTS,
    )

    assert candidates == ()
    assert authenticated_rpc_requirement(candidates) == {
        "classification": "AUTHENTICATED_RPC_REQUIRED",
        "accepted_env": list(AUTHENTICATED_RPC_ENV_ORDER),
    }


def test_authenticated_requirement_is_satisfied_by_one_secret_source() -> None:
    candidates = recent_rpc_candidates(
        require_authenticated=True,
        environ={"BSC_ARCHIVE_RPC_URL": "https://provider.example/secret"},
        public_endpoints=PUBLIC_ENDPOINTS,
    )

    assert len(candidates) == 1
    assert candidates[0].label == "env:BSC_ARCHIVE_RPC_URL"
    assert authenticated_rpc_requirement(candidates) is None
