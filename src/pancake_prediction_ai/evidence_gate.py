from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class EvidenceKind(StrEnum):
    STAGE5A_DRILL = "stage5a_drill"
    STAGE5B_FORK = "stage5b_fork"
    SHADOW_ECONOMICS = "shadow_economics"


class EvidenceOrigin(StrEnum):
    OBSERVED = "observed"
    ASSUMED = "assumed"
    SELF_REPORTED = "self_reported"


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: EvidenceKind
    origin: EvidenceOrigin
    passed: bool
    artifact_sha256: str
    recorded_at: str
    payload: Mapping[str, Any]

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "Evidence":
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("evidence JSON must be an object")
        declared = str(obj.get("artifact_sha256", ""))
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        actual = hashlib.sha256(canonical).hexdigest()
        if declared != actual:
            raise ValueError("artifact_sha256 does not match canonical payload")
        return cls(
            kind=EvidenceKind(str(obj["kind"])),
            origin=EvidenceOrigin(str(obj["origin"])),
            passed=bool(obj["passed"]),
            artifact_sha256=declared,
            recorded_at=str(obj["recorded_at"]),
            payload=payload,
        )

    @classmethod
    def from_path(cls, path: Path) -> "Evidence":
        return cls.from_json_bytes(path.read_bytes())

    @property
    def is_observed_pass(self) -> bool:
        return self.origin is EvidenceOrigin.OBSERVED and self.passed


@dataclass(frozen=True, slots=True)
class RuntimeSafetyState:
    kill_switch_armed: bool
    wallet_binding_ok: bool
    per_round_cap_ok: bool
    balance_cap_ok: bool
    unresolved_intents: int
    decision_window_open: bool
    signing_enabled: bool
    mainnet_broadcast_enabled: bool


@dataclass(frozen=True, slots=True)
class GateDecision:
    ready: bool
    blockers: tuple[str, ...]


def evaluate_stage6a_readiness(
    *,
    stage5a: Evidence,
    stage5b: Evidence,
    shadow: Evidence,
    safety: RuntimeSafetyState,
) -> GateDecision:
    blockers: list[str] = []

    expected = (
        (stage5a, EvidenceKind.STAGE5A_DRILL, "stage5a"),
        (stage5b, EvidenceKind.STAGE5B_FORK, "stage5b"),
        (shadow, EvidenceKind.SHADOW_ECONOMICS, "shadow"),
    )
    for evidence, kind, label in expected:
        if evidence.kind is not kind:
            blockers.append(f"{label}_wrong_evidence_kind")
        if not evidence.is_observed_pass:
            blockers.append(f"{label}_observed_pass_missing")

    if not safety.kill_switch_armed:
        blockers.append("kill_switch_not_armed")
    if not safety.wallet_binding_ok:
        blockers.append("wallet_binding_failed")
    if not safety.per_round_cap_ok:
        blockers.append("per_round_cap_failed")
    if not safety.balance_cap_ok:
        blockers.append("balance_cap_failed")
    if safety.unresolved_intents != 0:
        blockers.append("unresolved_intents_present")
    if not safety.decision_window_open:
        blockers.append("decision_window_closed")

    # Stage 6A is a preflight only. A signer/broadcaster becoming active here is itself a failure.
    if safety.signing_enabled:
        blockers.append("signing_enabled_during_preflight")
    if safety.mainnet_broadcast_enabled:
        blockers.append("mainnet_broadcast_enabled_during_preflight")

    return GateDecision(ready=not blockers, blockers=tuple(blockers))
