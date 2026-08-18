from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, cast

from .abi import function_selector
from .contracts import CHAIN_ID_BSC, MARKETS
from .execution_intent import IntentState
from .execution_report import build_execution_intent_report

EVIDENCE_VERSION = 3
BULL_SELECTOR = function_selector("betBull(uint256)").lower()
BEAR_SELECTOR = function_selector("betBear(uint256)").lower()
BET_CALLDATA_HEX_LENGTH = 2 + 8 + 64
REQUIRED_SCENARIOS = (
    "restart_recovery",
    "dropped_or_replaced_recovery",
    "reorg_reconciliation",
    "non_loopback_rejection",
)
REQUIRED_TRANSITIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "restart_recovery": (("submitting", "retryable"),),
    "dropped_or_replaced_recovery": (("submitted", "retryable"),),
    "reorg_reconciliation": (
        ("mined", "reorged"),
        ("reorged", "retryable"),
    ),
}


class EvidenceOrigin(StrEnum):
    OBSERVED = "observed"
    ASSUMED = "assumed"
    SELF_REPORTED = "self_reported"


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_sha256(value: str, *, field: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    try:
        int(normalized, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be a SHA-256 hex digest") from exc
    return normalized


def _validate_block_hash(value: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized) != 66:
        raise ValueError("fork_block_hash must be a 32-byte hex value")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError("fork_block_hash must be a 32-byte hex value") from exc
    return normalized


def _validate_source_sha(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 40:
        raise ValueError("source_sha must be a 40-character git SHA")
    try:
        int(normalized, 16)
    except ValueError as exc:
        raise ValueError("source_sha must be a 40-character git SHA") from exc
    return normalized


def _validate_recorded_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("recorded_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("recorded_at must include a timezone offset")
    return value


def _normalize_scenarios(
    scenarios: Mapping[str, bool],
) -> tuple[tuple[str, bool], ...]:
    normalized: dict[str, bool] = {}
    for key, value in scenarios.items():
        if not isinstance(value, bool):
            raise ValueError(f"scenario {key!r} must be boolean")
        normalized[str(key)] = value
    unknown = sorted(set(normalized) - set(REQUIRED_SCENARIOS))
    if unknown:
        raise ValueError(f"unknown Stage 5 scenario(s): {', '.join(unknown)}")
    missing = sorted(set(REQUIRED_SCENARIOS) - set(normalized))
    if missing:
        raise ValueError(f"missing Stage 5 scenario(s): {', '.join(missing)}")
    return tuple(sorted(normalized.items()))


@dataclass(frozen=True, slots=True)
class Stage5ForkEvidence:
    origin: EvidenceOrigin
    source_sha: str
    recorded_at: str
    campaign_id: str
    market: str
    epoch: int
    round_start_timestamp: int
    round_lock_timestamp: int
    chain_id: int
    fork_block_number: int
    fork_block_hash: str
    fork_block_timestamp: int
    anvil_version: str
    ledger_sha256: str
    scenarios: tuple[tuple[str, bool], ...]
    claim_sha256: str

    def claim_dict(self) -> dict[str, object]:
        return {
            "evidence_version": EVIDENCE_VERSION,
            "origin": self.origin.value,
            "source_sha": self.source_sha,
            "recorded_at": self.recorded_at,
            "campaign_id": self.campaign_id,
            "market": self.market,
            "epoch": self.epoch,
            "round_start_timestamp": self.round_start_timestamp,
            "round_lock_timestamp": self.round_lock_timestamp,
            "chain_id": self.chain_id,
            "fork_block_number": self.fork_block_number,
            "fork_block_hash": self.fork_block_hash,
            "fork_block_timestamp": self.fork_block_timestamp,
            "anvil_version": self.anvil_version,
            "ledger_sha256": self.ledger_sha256,
            "scenarios": dict(self.scenarios),
        }

    def as_dict(self) -> dict[str, object]:
        result = self.claim_dict()
        result["claim_sha256"] = self.claim_sha256
        return result

    @classmethod
    def create(
        cls,
        *,
        origin: EvidenceOrigin,
        source_sha: str,
        recorded_at: str,
        campaign_id: str,
        market: str,
        epoch: int,
        round_start_timestamp: int,
        round_lock_timestamp: int,
        chain_id: int,
        fork_block_number: int,
        fork_block_hash: str,
        fork_block_timestamp: int,
        anvil_version: str,
        ledger_sha256: str,
        scenarios: Mapping[str, bool],
    ) -> Stage5ForkEvidence:
        if not campaign_id:
            raise ValueError("campaign_id is required")
        if market not in MARKETS:
            raise ValueError(f"unsupported Prediction market: {market}")
        if epoch <= 0:
            raise ValueError("epoch must be positive")
        if round_start_timestamp <= 0:
            raise ValueError("round_start_timestamp must be positive")
        if round_lock_timestamp <= round_start_timestamp:
            raise ValueError("round_lock_timestamp must be after round_start_timestamp")
        if chain_id <= 0:
            raise ValueError("chain_id must be positive")
        if fork_block_number <= 0:
            raise ValueError("fork_block_number must be positive")
        if not (
            round_start_timestamp < fork_block_timestamp < round_lock_timestamp
        ):
            raise ValueError("fork block must be strictly inside the betting window")
        if not anvil_version.strip():
            raise ValueError("anvil_version is required")
        provisional = cls(
            origin=origin,
            source_sha=_validate_source_sha(source_sha),
            recorded_at=_validate_recorded_at(recorded_at),
            campaign_id=campaign_id,
            market=market,
            epoch=epoch,
            round_start_timestamp=round_start_timestamp,
            round_lock_timestamp=round_lock_timestamp,
            chain_id=chain_id,
            fork_block_number=fork_block_number,
            fork_block_hash=_validate_block_hash(fork_block_hash),
            fork_block_timestamp=fork_block_timestamp,
            anvil_version=anvil_version.strip(),
            ledger_sha256=_validate_sha256(ledger_sha256, field="ledger_sha256"),
            scenarios=_normalize_scenarios(scenarios),
            claim_sha256="0" * 64,
        )
        digest = _sha256_bytes(_canonical_json(provisional.claim_dict()))
        return cls(
            origin=provisional.origin,
            source_sha=provisional.source_sha,
            recorded_at=provisional.recorded_at,
            campaign_id=provisional.campaign_id,
            market=provisional.market,
            epoch=provisional.epoch,
            round_start_timestamp=provisional.round_start_timestamp,
            round_lock_timestamp=provisional.round_lock_timestamp,
            chain_id=provisional.chain_id,
            fork_block_number=provisional.fork_block_number,
            fork_block_hash=provisional.fork_block_hash,
            fork_block_timestamp=provisional.fork_block_timestamp,
            anvil_version=provisional.anvil_version,
            ledger_sha256=provisional.ledger_sha256,
            scenarios=provisional.scenarios,
            claim_sha256=digest,
        )

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> Stage5ForkEvidence:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Stage 5 evidence must be a JSON object")
        obj = cast(dict[str, Any], decoded)
        if int(obj.get("evidence_version", 0)) != EVIDENCE_VERSION:
            raise ValueError("unsupported Stage 5 evidence version")
        raw_scenarios = obj.get("scenarios")
        if not isinstance(raw_scenarios, dict):
            raise ValueError("scenarios must be an object")
        scenario_values: dict[str, bool] = {}
        for key, value in raw_scenarios.items():
            if not isinstance(value, bool):
                raise ValueError(f"scenario {key!r} must be boolean")
            scenario_values[str(key)] = value
        evidence = cls.create(
            origin=EvidenceOrigin(str(obj["origin"])),
            source_sha=str(obj["source_sha"]),
            recorded_at=str(obj["recorded_at"]),
            campaign_id=str(obj["campaign_id"]),
            market=str(obj["market"]),
            epoch=int(obj["epoch"]),
            round_start_timestamp=int(obj["round_start_timestamp"]),
            round_lock_timestamp=int(obj["round_lock_timestamp"]),
            chain_id=int(obj["chain_id"]),
            fork_block_number=int(obj["fork_block_number"]),
            fork_block_hash=str(obj["fork_block_hash"]),
            fork_block_timestamp=int(obj["fork_block_timestamp"]),
            anvil_version=str(obj["anvil_version"]),
            ledger_sha256=str(obj["ledger_sha256"]),
            scenarios=scenario_values,
        )
        declared = _validate_sha256(
            str(obj["claim_sha256"]), field="claim_sha256"
        )
        if declared != evidence.claim_sha256:
            raise ValueError(
                "claim_sha256 does not match the canonical Stage 5 claim"
            )
        return evidence

    @classmethod
    def from_path(cls, path: Path) -> Stage5ForkEvidence:
        return cls.from_json_bytes(path.read_bytes())


@dataclass(frozen=True, slots=True)
class Stage5ForkGateReport:
    ready: bool
    blockers: tuple[str, ...]
    total_intents: int
    unresolved_intents: int
    finalized_bull: int
    finalized_bear: int

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "total_intents": self.total_intents,
            "unresolved_intents": self.unresolved_intents,
            "finalized_bull": self.finalized_bull,
            "finalized_bear": self.finalized_bear,
        }


def ledger_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _calldata_epoch(calldata: str) -> int | None:
    if len(calldata) != BET_CALLDATA_HEX_LENGTH or not calldata.startswith("0x"):
        return None
    try:
        return int(calldata[10:], 16)
    except ValueError:
        return None


def _finalized_bet_counts(
    path: Path,
    *,
    market: str,
    epoch: int,
) -> tuple[int, int]:
    target = MARKETS[market].address.lower()
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT id,target,calldata,value_wei,state FROM execution_intents ORDER BY id"
        ).fetchall()
        bull = 0
        bear = 0
        for intent_id_raw, target_raw, calldata_raw, value_wei_raw, state_raw in rows:
            if IntentState(str(state_raw)) != IntentState.FINALIZED:
                continue
            if str(target_raw).lower() != target:
                continue
            if int(value_wei_raw) <= 0:
                continue
            calldata = str(calldata_raw).lower()
            if _calldata_epoch(calldata) != epoch:
                continue
            intent_id = int(intent_id_raw)
            submitted = conn.execute(
                """
                SELECT 1 FROM execution_attempts
                WHERE intent_id=? AND outcome='submitted' AND tx_hash IS NOT NULL
                LIMIT 1
                """,
                (intent_id,),
            ).fetchone()
            finalized_transition = conn.execute(
                """
                SELECT 1 FROM execution_transitions
                WHERE intent_id=? AND to_state=?
                LIMIT 1
                """,
                (intent_id, IntentState.FINALIZED.value),
            ).fetchone()
            if submitted is None or finalized_transition is None:
                continue
            if calldata.startswith(BULL_SELECTOR):
                bull += 1
            elif calldata.startswith(BEAR_SELECTOR):
                bear += 1
    return bull, bear


def _ledger_scenario_observations(
    path: Path,
) -> dict[str, tuple[bool, dict[str, Any] | None]]:
    try:
        with closing(sqlite3.connect(path)) as conn:
            rows = conn.execute(
                "SELECT scenario,observed,detail_json FROM execution_observations ORDER BY id"
            ).fetchall()
    except sqlite3.OperationalError:
        return {}

    observations: dict[str, tuple[bool, dict[str, Any] | None]] = {}
    for scenario_raw, observed_raw, detail_raw in rows:
        scenario = str(scenario_raw)
        try:
            detail = json.loads(str(detail_raw))
        except json.JSONDecodeError:
            observations[scenario] = (False, None)
            continue
        if not isinstance(detail, dict):
            observations[scenario] = (False, None)
            continue
        observations[scenario] = (bool(observed_raw), cast(dict[str, Any], detail))
    return observations


def _scenario_support_blockers(
    path: Path,
    *,
    scenario: str,
    detail: dict[str, Any] | None,
) -> list[str]:
    if scenario == "non_loopback_rejection":
        if detail is None or not isinstance(detail.get("probe_url"), str):
            return [f"scenario_detail_invalid:{scenario}"]
        return []

    if detail is None:
        return [f"scenario_detail_invalid:{scenario}"]
    intent_id_raw = detail.get("intent_id")
    if (
        isinstance(intent_id_raw, bool)
        or not isinstance(intent_id_raw, int)
        or intent_id_raw <= 0
    ):
        return [f"scenario_intent_missing:{scenario}"]
    intent_id = intent_id_raw

    blockers: list[str] = []
    with closing(sqlite3.connect(path)) as conn:
        transition_rows = conn.execute(
            """
            SELECT from_state,to_state
            FROM execution_transitions
            WHERE intent_id=? AND from_state IS NOT NULL
            ORDER BY id
            """,
            (intent_id,),
        ).fetchall()
        transitions = {
            (str(from_state), str(to_state))
            for from_state, to_state in transition_rows
        }
        for transition in REQUIRED_TRANSITIONS.get(scenario, ()):
            if transition not in transitions:
                blockers.append(
                    "scenario_transition_missing:"
                    f"{scenario}:{transition[0]}->{transition[1]}"
                )

        attempt_rows = conn.execute(
            """
            SELECT outcome,tx_hash
            FROM execution_attempts
            WHERE intent_id=?
            ORDER BY attempt_number
            """,
            (intent_id,),
        ).fetchall()
        outcomes = [str(outcome) for outcome, _ in attempt_rows]
        submitted_hashes = {
            str(tx_hash).lower()
            for outcome, tx_hash in attempt_rows
            if str(outcome) == "submitted" and tx_hash is not None
        }
        intent_row = conn.execute(
            "SELECT nonce FROM execution_intents WHERE id=?",
            (intent_id,),
        ).fetchone()

    if intent_row is None:
        blockers.append(f"scenario_intent_missing:{scenario}")
        return blockers

    if scenario == "restart_recovery":
        if "interrupted" not in outcomes:
            blockers.append("scenario_attempt_missing:restart_recovery:interrupted")
        if detail.get("attempt_outcome") != "interrupted":
            blockers.append("scenario_detail_invalid:restart_recovery")
        return blockers

    old_key = (
        "dropped_tx_hash"
        if scenario == "dropped_or_replaced_recovery"
        else "reorged_tx_hash"
    )
    old_hash = detail.get(old_key)
    replacement_hash = detail.get("replacement_tx_hash")
    if not isinstance(old_hash, str) or not isinstance(replacement_hash, str):
        blockers.append(f"scenario_detail_invalid:{scenario}")
        return blockers
    old_hash = old_hash.lower()
    replacement_hash = replacement_hash.lower()
    if old_hash == replacement_hash:
        blockers.append(f"scenario_replacement_not_distinct:{scenario}")
    if old_hash not in submitted_hashes:
        blockers.append(f"scenario_attempt_hash_missing:{scenario}:original")
    if replacement_hash not in submitted_hashes:
        blockers.append(f"scenario_attempt_hash_missing:{scenario}:replacement")

    nonce_raw = detail.get("reserved_nonce")
    current_nonce = intent_row[0]
    if (
        isinstance(nonce_raw, bool)
        or not isinstance(nonce_raw, int)
        or current_nonce is None
        or int(current_nonce) != nonce_raw
    ):
        blockers.append(f"scenario_nonce_mismatch:{scenario}")
    return blockers


def evaluate_stage5b_fork_gate(
    *,
    ledger_path: Path,
    evidence: Stage5ForkEvidence,
    expected_source_sha: str | None = None,
) -> Stage5ForkGateReport:
    report = build_execution_intent_report(ledger_path)
    bull, bear = _finalized_bet_counts(
        ledger_path,
        market=evidence.market,
        epoch=evidence.epoch,
    )
    observations = _ledger_scenario_observations(ledger_path)
    blockers: list[str] = []

    if report.total == 0:
        blockers.append("empty_execution_campaign")
    if report.unresolved != 0:
        blockers.append("unresolved_execution_intents")
    if bull == 0:
        blockers.append("finalized_bull_missing")
    if bear == 0:
        blockers.append("finalized_bear_missing")
    if evidence.origin is not EvidenceOrigin.OBSERVED:
        blockers.append("evidence_not_observed")
    if evidence.chain_id != CHAIN_ID_BSC:
        blockers.append("fork_chain_id_not_bsc")
    if evidence.ledger_sha256 != ledger_sha256(ledger_path):
        blockers.append("ledger_hash_mismatch")
    if expected_source_sha is not None:
        expected = _validate_source_sha(expected_source_sha)
        if evidence.source_sha != expected:
            blockers.append("source_sha_mismatch")

    scenario_map = dict(evidence.scenarios)
    for scenario in REQUIRED_SCENARIOS:
        if not scenario_map[scenario]:
            blockers.append(f"scenario_not_claimed:{scenario}")
        observed, detail = observations.get(scenario, (False, None))
        if not observed:
            blockers.append(f"scenario_not_observed_in_ledger:{scenario}")
        blockers.extend(
            _scenario_support_blockers(
                ledger_path,
                scenario=scenario,
                detail=detail,
            )
        )

    return Stage5ForkGateReport(
        ready=not blockers,
        blockers=tuple(blockers),
        total_intents=report.total,
        unresolved_intents=report.unresolved,
        finalized_bull=bull,
        finalized_bear=bear,
    )
