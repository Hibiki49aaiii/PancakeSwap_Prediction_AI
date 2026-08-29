from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .shadow_ledger import ShadowLedgerAuditReport

PPM = 1_000_000


@dataclass(frozen=True, slots=True)
class ShadowCampaignPolicy:
    min_predictions: int = 1_000
    min_settlements: int = 900
    min_probability_scored: int = 900
    min_actionable_predictions: int = 200
    min_decision_span_ms: int = 7 * 24 * 60 * 60 * 1_000
    max_unresolved_ppm: int = 100_000
    require_both_directions: bool = True
    require_complete_actionable_pnl: bool = True
    max_model_ids: int = 1
    max_feature_set_ids: int = 1

    def validate(self) -> None:
        for field in (
            "min_predictions",
            "min_settlements",
            "min_probability_scored",
            "min_actionable_predictions",
            "min_decision_span_ms",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if not 0 <= self.max_unresolved_ppm <= PPM:
            raise ValueError("max_unresolved_ppm must be in [0, 1_000_000]")
        if self.max_model_ids <= 0:
            raise ValueError("max_model_ids must be positive")
        if self.max_feature_set_ids <= 0:
            raise ValueError("max_feature_set_ids must be positive")


@dataclass(frozen=True, slots=True)
class ShadowCampaignGateReport:
    policy: ShadowCampaignPolicy
    audit: ShadowLedgerAuditReport
    checks: dict[str, bool]
    unresolved_ppm: int
    settlement_coverage_ppm: int
    actionable_pnl_coverage_ppm: int

    @property
    def gate_ready(self) -> bool:
        return all(self.checks.values())

    @property
    def campaign_digest(self) -> str:
        payload = {
            "policy": asdict(self.policy),
            "ledger_head_digest": self.audit.head_digest,
            "ledger_event_count": self.audit.event_count,
            "campaign_manifest_digest": self.audit.campaign_manifest_digest,
            "checks": self.checks,
            "unresolved_ppm": self.unresolved_ppm,
            "settlement_coverage_ppm": self.settlement_coverage_ppm,
            "actionable_pnl_coverage_ppm": self.actionable_pnl_coverage_ppm,
        }
        raw = (
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": asdict(self.policy),
            "audit": self.audit.as_dict(),
            "checks": dict(self.checks),
            "gate_ready": self.gate_ready,
            "campaign_digest": self.campaign_digest,
            "unresolved_ppm": self.unresolved_ppm,
            "settlement_coverage_ppm": self.settlement_coverage_ppm,
            "actionable_pnl_coverage_ppm": self.actionable_pnl_coverage_ppm,
            "profitability_gate_eligible": False,
            "full_historical_gate_satisfied": False,
            "signing_enabled": False,
            "live_broadcast": False,
            "interpretation": (
                "Stage 4 shadow operational-readiness evidence only. Positive PnL, directional "
                "accuracy, or a low Brier score is not itself a pass condition and "
                "does not establish "
                "durable profitability."
            ),
        }


def _ratio_ppm(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return (numerator * PPM) // denominator


def evaluate_shadow_campaign(
    audit: ShadowLedgerAuditReport,
    policy: ShadowCampaignPolicy | None = None,
) -> ShadowCampaignGateReport:
    selected = policy or ShadowCampaignPolicy()
    selected.validate()

    unresolved_ppm = _ratio_ppm(audit.unresolved_count, audit.prediction_count)
    settlement_coverage_ppm = _ratio_ppm(
        audit.settlement_count,
        audit.prediction_count,
    )
    actionable_pnl_coverage_ppm = _ratio_ppm(
        audit.observed_pnl_count,
        audit.settled_actionable_count,
    )

    bull_count = int(audit.action_counts.get("bull", 0))
    bear_count = int(audit.action_counts.get("bear", 0))
    checks = {
        "ledger_integrity_ready": audit.integrity_ready,
        "campaign_manifest_bound": audit.campaign_manifest_digest is not None,
        "prediction_count_sufficient": audit.prediction_count >= selected.min_predictions,
        "settlement_count_sufficient": audit.settlement_count >= selected.min_settlements,
        "probability_scored_sufficient": (
            audit.probability_scored_count >= selected.min_probability_scored
        ),
        "actionable_prediction_count_sufficient": (
            audit.actionable_prediction_count >= selected.min_actionable_predictions
        ),
        "decision_span_sufficient": audit.decision_span_ms >= selected.min_decision_span_ms,
        "unresolved_rate_within_limit": unresolved_ppm <= selected.max_unresolved_ppm,
        "both_directions_observed": (
            not selected.require_both_directions or (bull_count > 0 and bear_count > 0)
        ),
        "actionable_pnl_complete": (
            not selected.require_complete_actionable_pnl
            or (
                audit.settled_actionable_count > 0
                and audit.observed_pnl_count == audit.settled_actionable_count
            )
        ),
        "model_version_count_within_limit": len(audit.model_ids) <= selected.max_model_ids,
        "feature_set_count_within_limit": (
            len(audit.feature_set_ids) <= selected.max_feature_set_ids
        ),
    }
    return ShadowCampaignGateReport(
        policy=selected,
        audit=audit,
        checks=checks,
        unresolved_ppm=unresolved_ppm,
        settlement_coverage_ppm=settlement_coverage_ppm,
        actionable_pnl_coverage_ppm=actionable_pnl_coverage_ppm,
    )


_EVIDENCE_ROLES = {"latest_attempt", "last_success"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_shadow_campaign_evidence(
    ledger_path: Path,
    campaign: ShadowCampaignGateReport,
    *,
    evidence_role: str = "latest_attempt",
) -> dict[str, object]:
    if evidence_role not in _EVIDENCE_ROLES:
        raise ValueError("evidence_role must be latest_attempt or last_success")
    if evidence_role == "last_success" and not campaign.gate_ready:
        raise ValueError("last_success evidence requires a ready campaign")
    if campaign.audit.campaign_manifest_digest is None:
        raise ValueError("campaign evidence requires a bound campaign manifest")
    if not ledger_path.is_file():
        raise ValueError(f"shadow ledger does not exist: {ledger_path}")

    return {
        "evidence_version": 1,
        "evidence_role": evidence_role,
        "purpose": "stage4_shadow_campaign_operational_readiness",
        "success": campaign.gate_ready,
        "workflow_outcome": "success" if campaign.gate_ready else "incomplete",
        "ledger_sha256": _sha256_file(ledger_path),
        "ledger_binding": {
            "campaign_manifest_digest": campaign.audit.campaign_manifest_digest,
            "event_count": campaign.audit.event_count,
            "head_digest": campaign.audit.head_digest,
            "campaign_digest": campaign.campaign_digest,
            "physical_sha256_scope": "sqlite_main_database_file",
        },
        "campaign": campaign.as_dict(),
        "profitability_gate_eligible": False,
        "full_historical_gate_satisfied": False,
        "signing_enabled": False,
        "live_broadcast": False,
        "funded_execution": False,
        "interpretation": (
            "Stage 4 shadow operational-readiness evidence only. This proves append-only "
            "decision capture, settlement reconciliation, minimum campaign coverage, and "
            "metric availability. PnL sign is intentionally not a pass condition and no "
            "funded execution is authorized."
        ),
    }
