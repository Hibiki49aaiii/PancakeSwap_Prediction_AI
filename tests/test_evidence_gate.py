from __future__ import annotations

import hashlib
import json

import pytest

from pancake_prediction_ai.evidence_gate import (
    Evidence,
    EvidenceKind,
    EvidenceOrigin,
    RuntimeSafetyState,
    evaluate_stage6a_readiness,
)


def evidence(kind: EvidenceKind, *, origin: EvidenceOrigin = EvidenceOrigin.OBSERVED, passed: bool = True) -> Evidence:
    payload = {"kind": kind.value, "test": "fixture", "passed": passed}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return Evidence(kind, origin, passed, digest, "2026-08-19T00:00:00+09:00", payload)


def safety(**changes: object) -> RuntimeSafetyState:
    values = dict(
        kill_switch_armed=True,
        wallet_binding_ok=True,
        per_round_cap_ok=True,
        balance_cap_ok=True,
        unresolved_intents=0,
        decision_window_open=True,
        signing_enabled=False,
        mainnet_broadcast_enabled=False,
    )
    values.update(changes)
    return RuntimeSafetyState(**values)  # type: ignore[arg-type]


def test_all_observed_evidence_and_safety_can_clear_stage6a() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=evidence(EvidenceKind.SHADOW_ECONOMICS),
        safety=safety(),
    )
    assert decision.ready
    assert decision.blockers == ()


@pytest.mark.parametrize("origin", [EvidenceOrigin.ASSUMED, EvidenceOrigin.SELF_REPORTED])
def test_non_observed_stage5b_never_clears_gate(origin: EvidenceOrigin) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK, origin=origin),
        shadow=evidence(EvidenceKind.SHADOW_ECONOMICS),
        safety=safety(),
    )
    assert not decision.ready
    assert "stage5b_observed_pass_missing" in decision.blockers


def test_shadow_profitability_must_be_observed_not_assumed() -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=evidence(EvidenceKind.SHADOW_ECONOMICS, origin=EvidenceOrigin.ASSUMED),
        safety=safety(),
    )
    assert not decision.ready
    assert "shadow_observed_pass_missing" in decision.blockers


@pytest.mark.parametrize(
    ("changes", "blocker"),
    [
        ({"kill_switch_armed": False}, "kill_switch_not_armed"),
        ({"wallet_binding_ok": False}, "wallet_binding_failed"),
        ({"per_round_cap_ok": False}, "per_round_cap_failed"),
        ({"balance_cap_ok": False}, "balance_cap_failed"),
        ({"unresolved_intents": 1}, "unresolved_intents_present"),
        ({"decision_window_open": False}, "decision_window_closed"),
        ({"signing_enabled": True}, "signing_enabled_during_preflight"),
        ({"mainnet_broadcast_enabled": True}, "mainnet_broadcast_enabled_during_preflight"),
    ],
)
def test_each_runtime_safety_failure_blocks(changes: dict[str, object], blocker: str) -> None:
    decision = evaluate_stage6a_readiness(
        stage5a=evidence(EvidenceKind.STAGE5A_DRILL),
        stage5b=evidence(EvidenceKind.STAGE5B_FORK),
        shadow=evidence(EvidenceKind.SHADOW_ECONOMICS),
        safety=safety(**changes),
    )
    assert not decision.ready
    assert blocker in decision.blockers


def test_evidence_json_requires_payload_hash_match() -> None:
    raw = json.dumps(
        {
            "kind": "stage5b_fork",
            "origin": "observed",
            "passed": True,
            "recorded_at": "2026-08-19T00:00:00+09:00",
            "artifact_sha256": "0" * 64,
            "payload": {"fork_executed": True},
        }
    ).encode()
    with pytest.raises(ValueError, match="artifact_sha256"):
        Evidence.from_json_bytes(raw)
