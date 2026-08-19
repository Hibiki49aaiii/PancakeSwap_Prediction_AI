from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from .runtime_fingerprint import (
    capture_runtime_fingerprint,
    fingerprint_sha256,
    validate_runtime_fingerprint_payload,
)


class EvidenceKind(StrEnum):
    STAGE5A_DRILL = "stage5a_drill"
    STAGE5B_FORK = "stage5b_fork"
    SHADOW_ECONOMICS = "shadow_economics"


class EvidenceOrigin(StrEnum):
    OBSERVED = "observed"
    HYBRID = "hybrid"
    ASSUMED = "assumed"
    SELF_REPORTED = "self_reported"


STAGE5A_DRILL_SCHEMA = "stage5a_execution_drill_v2"
STAGE5B_FORK_SCHEMA = "stage5b_verified_local_bsc_fork_v1"
SHADOW_GATE_SCHEMA = "shadow_gate_evidence_v1"


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
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
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


def _payload_hash_matches(evidence: Evidence) -> bool:
    try:
        canonical = json.dumps(
            evidence.payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError):
        return False
    return hashlib.sha256(canonical).hexdigest() == evidence.artifact_sha256


def _strict_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _valid_block_hash(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 66 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def _qualified_stage5a_pass(evidence: Evidence) -> bool:
    """Accept only current-runtime evidence emitted by the Stage 5A drill schema."""

    if evidence.kind is not EvidenceKind.STAGE5A_DRILL:
        return False
    if evidence.origin is not EvidenceOrigin.OBSERVED or not evidence.passed:
        return False
    if not _payload_hash_matches(evidence):
        return False
    payload = evidence.payload
    if payload.get("schema") != STAGE5A_DRILL_SCHEMA:
        return False
    if payload.get("drill_type") != "local_sqlite_execution_state_durability":
        return False
    if payload.get("blockchain_transaction_created") is not False:
        return False
    if payload.get("transaction_signed") is not False:
        return False
    if payload.get("transaction_broadcast") is not False:
        return False

    runtime_payload = payload.get("runtime_fingerprint")
    runtime_sha = payload.get("runtime_fingerprint_sha256")
    if not validate_runtime_fingerprint_payload(runtime_payload):
        return False
    if not _valid_sha256(runtime_sha):
        return False
    if fingerprint_sha256(runtime_payload) != runtime_sha:
        return False
    # A Stage 5A drill from another Python/OS/architecture/SQLite stack cannot
    # clear preflight on this process. The official gate therefore re-captures
    # the current runtime rather than trusting a caller-supplied environment ID.
    if capture_runtime_fingerprint().sha256 != runtime_sha:
        return False

    required_true = (
        "journal_mode_wal",
        "synchronous_full",
        "unresolved_recovered_after_restart",
        "duplicate_active_nonce_rejected",
        "unknown_state_persisted_after_missing_receipt",
        "finalized_state_persisted_after_confirmations",
        "terminal_nonce_released",
        "terminal_reuse_cleanup_persisted",
    )
    if any(payload.get(key) is not True for key in required_true):
        return False
    unresolved = _strict_int(payload, "unresolved_count_final")
    required_confirmations = _strict_int(payload, "required_confirmations")
    if unresolved != 0:
        return False
    if required_confirmations is None or required_confirmations < 1:
        return False
    return True


def _qualified_stage5b_pass(evidence: Evidence) -> bool:
    """Accept only a local BSC fork probe independently matched to upstream BSC."""

    if evidence.kind is not EvidenceKind.STAGE5B_FORK:
        return False
    if evidence.origin is not EvidenceOrigin.OBSERVED or not evidence.passed:
        return False
    if not _payload_hash_matches(evidence):
        return False
    payload = evidence.payload
    if payload.get("schema") != STAGE5B_FORK_SCHEMA:
        return False
    if payload.get("probe_type") != "verified_local_bsc_fork":
        return False
    if payload.get("transaction_signed") is not False:
        return False
    if payload.get("mainnet_transaction_broadcast") is not False:
        return False

    chain_id = _strict_int(payload, "chain_id")
    upstream_chain_id = _strict_int(payload, "upstream_chain_id")
    initial_block = _strict_int(payload, "initial_block")
    mined_block = _strict_int(payload, "mined_block")
    reset_block = _strict_int(payload, "reset_block")
    if chain_id != 56 or upstream_chain_id != 56:
        return False
    if initial_block is None or initial_block <= 0:
        return False
    if mined_block is None or mined_block < initial_block + 1:
        return False
    if reset_block != initial_block:
        return False

    required_true = (
        "prediction_contract_code_present",
        "chainlink_contract_code_present",
        "prediction_code_present_after_reset",
        "chainlink_code_present_after_reset",
        "fork_reset_supported",
        "fork_mine_observed",
        "fork_block_hash_matches_upstream",
        "reset_block_hash_matches_upstream",
        "prediction_code_matches_upstream",
        "chainlink_code_matches_upstream",
        "prediction_code_matches_upstream_after_reset",
        "chainlink_code_matches_upstream_after_reset",
        "upstream_verified",
    )
    if any(payload.get(key) is not True for key in required_true):
        return False

    local_initial_hash = payload.get("local_initial_block_hash")
    upstream_hash = payload.get("upstream_fork_block_hash")
    local_reset_hash = payload.get("local_reset_block_hash")
    if not all(_valid_block_hash(value) for value in (local_initial_hash, upstream_hash, local_reset_hash)):
        return False
    if not (local_initial_hash == upstream_hash == local_reset_hash):
        return False
    return True


def _qualified_shadow_pass(evidence: Evidence) -> bool:
    """Accept only explicitly qualified hybrid paper/shadow economics evidence.

    Shadow economics combines observed market/settlement state with simulated
    execution. Treating an arbitrary `origin=observed, passed=true` JSON as a
    Stage-6 prerequisite would erase that distinction. The gate therefore
    requires a dedicated schema whose policy/metrics can be rechecked locally.

    Two completeness requirements are non-negotiable at Stage 6A: every paper
    decision represented by the evidence must be settled, and claim/refund gas
    must be modeled whenever that operation is required. A supplied policy may
    tighten thresholds, but it cannot turn either requirement off.
    """

    if evidence.kind is not EvidenceKind.SHADOW_ECONOMICS:
        return False
    if evidence.origin is not EvidenceOrigin.HYBRID or not evidence.passed:
        return False
    if not _payload_hash_matches(evidence):
        return False
    payload = evidence.payload
    if payload.get("schema") != SHADOW_GATE_SCHEMA:
        return False
    if payload.get("shadow_artifact_schema") != "shadow_economic_evidence_v1":
        return False
    if payload.get("artifact_class") != "hybrid_shadow_not_live":
        return False
    if payload.get("source_event_store_availability") != "observed":
        return False
    if payload.get("funded_live_profitability_evidence") is not False:
        return False
    if payload.get("stage6b_funded_validation_evidence") is not False:
        return False
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or blockers:
        return False
    policy = payload.get("acceptance_policy")
    metrics = payload.get("metrics")
    if not isinstance(policy, dict) or not isinstance(metrics, dict):
        return False

    # These may not be weakened by a caller-provided acceptance policy.
    if policy.get("require_all_decisions_settled") is not True:
        return False
    if policy.get("require_fully_costed_claim_or_refund_gas") is not True:
        return False

    min_rounds = policy.get("min_settled_rounds")
    settled = metrics.get("settled_rounds")
    unresolved = metrics.get("unresolved_rounds")
    conditional_pnl = metrics.get("conditional_net_pnl_wei")
    conditional_dd = metrics.get("conditional_max_drawdown_wei")
    min_pnl = policy.get("min_conditional_net_pnl_wei")
    max_dd = policy.get("max_conditional_drawdown_wei")
    if (
        isinstance(min_rounds, bool)
        or not isinstance(min_rounds, int)
        or min_rounds <= 0
        or isinstance(settled, bool)
        or not isinstance(settled, int)
        or settled < min_rounds
        or isinstance(unresolved, bool)
        or not isinstance(unresolved, int)
        or unresolved != 0
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
    if metrics.get("claim_or_refund_gas_fully_modeled") is not True:
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

    if stage5a.kind is not EvidenceKind.STAGE5A_DRILL:
        blockers.append("stage5a_wrong_evidence_kind")
    if not _qualified_stage5a_pass(stage5a):
        blockers.append("stage5a_qualified_observed_pass_missing")

    if stage5b.kind is not EvidenceKind.STAGE5B_FORK:
        blockers.append("stage5b_wrong_evidence_kind")
    if not _qualified_stage5b_pass(stage5b):
        blockers.append("stage5b_qualified_observed_pass_missing")

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
