from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from .evidence_gate import Evidence, EvidenceKind, EvidenceOrigin
from .shadow_evidence_artifact import ShadowEconomicEvidenceArtifact


SHADOW_GATE_EVIDENCE_SCHEMA = "shadow_gate_evidence_v1"


@dataclass(frozen=True, slots=True)
class ShadowGateAcceptancePolicy:
    min_settled_rounds: int
    min_conditional_net_pnl_wei: int
    max_conditional_drawdown_wei: int
    min_average_selected_expected_return: float
    require_all_decisions_settled: bool = True
    require_fully_costed_claim_or_refund_gas: bool = True

    def validate(self) -> None:
        if self.min_settled_rounds <= 0:
            raise ValueError("min_settled_rounds must be positive")
        if self.max_conditional_drawdown_wei < 0:
            raise ValueError("max_conditional_drawdown_wei must be non-negative")
        if not math.isfinite(self.min_average_selected_expected_return):
            raise ValueError("min_average_selected_expected_return must be finite")


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def build_shadow_gate_evidence(
    artifact: ShadowEconomicEvidenceArtifact,
    *,
    policy: ShadowGateAcceptancePolicy,
    recorded_at: str,
) -> Evidence:
    """Convert one hybrid shadow artifact into policy-qualified gate evidence.

    The returned origin is HYBRID by construction. This is intentional: market,
    pool and settlement observations are real, while bet execution and some cost
    inputs remain simulated/assumed. Passing this evidence can support Stage 6A
    preflight only; it never represents funded live profitability evidence.
    """

    artifact.validate()
    policy.validate()
    if not recorded_at:
        raise ValueError("recorded_at is required")

    classification = artifact.payload["evidence_classification"]
    source = artifact.payload["source_event_store"]
    summary = artifact.payload["summary"]
    completeness = artifact.payload["completeness"]
    assert isinstance(classification, dict)
    assert isinstance(source, dict)
    assert isinstance(summary, dict)
    assert isinstance(completeness, dict)

    blockers: list[str] = []
    settled_rounds = int(summary["settled_rounds"])
    unresolved_rounds = int(summary["unresolved_rounds"])
    conditional_net_pnl_wei = int(summary["conditional_net_pnl_wei"])
    conditional_max_drawdown_wei = int(summary["conditional_max_drawdown_wei"])
    average_expected_raw = summary["average_selected_expected_return"]
    average_expected = (
        None if average_expected_raw is None else float(average_expected_raw)
    )
    claim_gas_modeled = bool(summary["claim_or_refund_gas_fully_modeled"])

    if classification.get("artifact_class") != "hybrid_shadow_not_live":
        blockers.append("shadow_artifact_class_invalid")
    if source.get("availability_mode") != "observed":
        blockers.append("shadow_source_not_observed")
    if source.get("hash_chain_verified") is not True:
        blockers.append("shadow_source_hash_chain_unverified")
    if settled_rounds < policy.min_settled_rounds:
        blockers.append("shadow_settled_rounds_below_policy")
    if (
        policy.require_all_decisions_settled
        and unresolved_rounds != 0
    ):
        blockers.append("shadow_unresolved_rounds_present")
    if conditional_net_pnl_wei < policy.min_conditional_net_pnl_wei:
        blockers.append("shadow_net_pnl_below_policy")
    if conditional_max_drawdown_wei > policy.max_conditional_drawdown_wei:
        blockers.append("shadow_drawdown_above_policy")
    if average_expected is None:
        blockers.append("shadow_average_expected_return_missing")
    elif average_expected < policy.min_average_selected_expected_return:
        blockers.append("shadow_average_expected_return_below_policy")
    if (
        policy.require_fully_costed_claim_or_refund_gas
        and not claim_gas_modeled
    ):
        blockers.append("shadow_claim_or_refund_gas_incomplete")

    payload: dict[str, object] = {
        "schema": SHADOW_GATE_EVIDENCE_SCHEMA,
        "shadow_evidence_artifact_sha256": artifact.artifact_sha256,
        "shadow_artifact_schema": str(artifact.payload["schema"]),
        "artifact_class": str(classification["artifact_class"]),
        "source_event_store_availability": str(source["availability_mode"]),
        "source_event_store_tip_hash": str(source["tip_hash"]),
        "acceptance_policy": asdict(policy),
        "metrics": {
            "settled_rounds": settled_rounds,
            "unresolved_rounds": unresolved_rounds,
            "conditional_net_pnl_wei": conditional_net_pnl_wei,
            "conditional_max_drawdown_wei": conditional_max_drawdown_wei,
            "average_selected_expected_return": average_expected,
            "claim_or_refund_gas_fully_modeled": claim_gas_modeled,
        },
        "blockers": blockers,
        "funded_live_profitability_evidence": False,
        "stage6b_funded_validation_evidence": False,
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return Evidence(
        kind=EvidenceKind.SHADOW_ECONOMICS,
        origin=EvidenceOrigin.HYBRID,
        passed=not blockers,
        artifact_sha256=digest,
        recorded_at=recorded_at,
        payload=payload,
    )
