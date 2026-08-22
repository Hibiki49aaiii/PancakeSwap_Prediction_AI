from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.run_recent_public_bootstrap import (
    AUTHENTICATED_RPC_ENV_ORDER,
    PUBLIC_BSC_ENDPOINTS,
    authenticated_rpc_candidates,
    main,
    rpc_candidates,
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

    candidates = rpc_candidates(require_authenticated=False, environ=environment)

    assert candidates[0].label == "env:BSC_LOG_RPC_URL"
    assert candidates[0].authenticated is True
    assert tuple(candidate.label for candidate in candidates[1:]) == PUBLIC_BSC_ENDPOINTS
    assert all(candidate.authenticated is False for candidate in candidates[1:])


def test_authenticated_only_mode_never_falls_back_to_public_endpoints() -> None:
    candidates = rpc_candidates(require_authenticated=True, environ={})
    assert candidates == ()


def test_authenticated_only_main_fails_fast_without_secret(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for variable in AUTHENTICATED_RPC_ENV_ORDER:
        monkeypatch.delenv(variable, raising=False)

    database = tmp_path / "recent.sqlite"
    output = tmp_path / "recent.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_recent_public_bootstrap.py",
            "--market",
            "BNBUSD",
            "--database",
            str(database),
            "--output",
            str(output),
            "--start-timestamp",
            "1786838400",
            "--end-timestamp",
            "1787097600",
            "--require-authenticated-rpc",
        ],
    )

    assert main() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["success"] is False
    assert payload["attempts"] == []
    assert payload["selected"] is None
    assert payload["rpc_policy"]["source_mode"] == "authenticated_only"
    assert payload["source_requirement"] == {
        "classification": "AUTHENTICATED_RPC_REQUIRED",
        "accepted_env": ["BSC_LOG_RPC_URL", "BSC_ARCHIVE_RPC_URL"],
    }
    assert database.exists() is False
