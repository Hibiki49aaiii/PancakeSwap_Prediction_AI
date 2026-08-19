from __future__ import annotations

import hashlib
import json
import math
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
    HYBRID = "hybrid"
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


def _number(mapping: Mapping[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _qualified_shadow_pass(evidence: Evidence) -> bool:
    """Accept only explicitly qualified hybrid paper/shadow economics evidence.

    Shadow economics combines observed market/settlement state with simulated
    execution. Treating an arbitrary `origin=observed, passed=true` JSON as a
    Stage-6 prerequisite would erase that distinction. The gate therefore
    requires a dedicated schema whose policy/metrics can be rechecked locally.
    """

    if evidence.kind is not EvidenceKind.SHADOW_ECONOMICS:
        return False
    if evidence.origin is not EvidenceOrigin.HYBRID or not evidence.passed:
        return False
    payload = evidence.payload
    if payload.get("schema") != "shadow_gate_evidence_v1":
        return False
    if payload.get("shadow_artifact_schema") != "shadow_economic_evidence_v1":
        return False
    if payload.get("artifact_class") != "hybrid_shadow_not_live":
        return False
    if payload.get("source_event_store_availability") != "observed":
        return False
    if payload.get("funded_live_profitability_evidence") is not False:
        return False
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or blockers:
        return False
    policy = payload.get("acceptance_policy")
    metrics = payload.get("metrics")
    if not isinstance(policy, dict) or not isinstance(metrics, dict):
        return False

    min_rounds = policy.get("min_settled_rounds")
    settled = metrics.get("settled_rounds")
    unresolved = metrics.get("unresolved_rounds")
    conditional_pnl = metrics.get("conditional_net_pnl_wei")
    conditional_dd = metrics.get("conditional_max_drawdown_wei")
    average_expected = metrics.get("average_selected_expected_return")
    min_pnl = policy.get("min_conditional_net_pnl_wei")
    max_dd = policy.get("max_conditional_drawdown_wei")
    min_expected = policy.get("min_average_selected_expected_return")
    if (
        isinstance(min_rounds, bool)
        or not isinstance(min_rounds, int)
        or min_rounds <= 0
        or isinstance(settled, bool)
        or not isinstance(settled, int)
        or settled < min_rounds
        or isinstance(unresolved, bool)
        or not isinstance(unresolved, int)
        or unresolved < 0
        or isinstance(conditional_pnl, bool)
        or not isinstance(conditional_pnl, int)
        or isinstance(conditional_dd, bool)
        or not isinstance(conditional_dd, int)
        or conditional_dd < 0
        or isinstance(min_pnl, bool)
        or not isinstance(min_pnl, int)
        or isinstance(max_dd, bool)
        or not isinstance(max_dd, int)
        or max_dd < 0
    ):
        return False
    min_expected_num = _number(policy, "min_average_selected_expected_return")
    average_expected_num = _number(metrics, "average_selected_expected_return")
    if min_expected_num is None or average_expected_num is None:
        return False
    if conditional_pnl < min_pnl or conditional_dd > max_dd:
        return False
    if average_expected_num < min_expected_num:
        return False
    if policy.get("require_all_decisions_settled") is True and unresolved != 0:
        return False
    if (
        policy.get("require_fully_costed_claim_or_refund_gas") is True
        and metrics.get("claim_or_refund_gas_fully_modeled") is not True
    ):
        return False
    return True


def evaluate_stage6a_readiness(
    *,
    stage5a: Evidence,
    stage5b: Evidence,
    shadow: Evidence,
    safety: RuntimeSafetyState,
) -> GateDecision:
    blockers: list[str] = []

    for evidence, kind, label in (
        (stage5a, EvidenceKind.STAGE5A_DRILL, "stage5a"),
        (stage5b, EvidenceKind.STAGE5B_FORK, "stage5b"),
    ):
        if evidence.kind is not kind:
            blockers.append(f"{label}_wrong_evidence_kind")
        if not evidence.is_observed_pass:
            blockers.append(f"{label}_observed_pass_missing")

    if shadow.kind is not EvidenceKind.SHADOW_ECONOMICS:
        blockers.append("shadow_wrong_evidence_kind")
    if not _qualified_shadow_pass(shadow):
        blockers.append("shadow_qualified_hybrid_pass_missing")

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