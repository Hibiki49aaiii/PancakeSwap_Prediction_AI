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

EVIDENCE_VERSION = 2
BULL_SELECTOR = function_selector("betBull(uint256)").lower()
BEAR_SELECTOR = function_selector("betBear(uint256)").lower()
BET_CALLDATA_HEX_LENGTH = 2 + 8 + 64
REQUIRED_SCENARIOS = (
    "restart_recovery",
    "dropped_or_replaced_recovery",
    "reorg_reconciliation",
    "non_loopback_rejection",
)


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
    chain_id: int
    fork_block_number: int
    fork_block_hash: str
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
            "chain_id": self.chain_id,
            "fork_block_number": self.fork_block_number,
            "fork_block_hash": self.fork_block_hash,
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
        chain_id: int,
        fork_block_number: int,
        fork_block_hash: str,
        anvil_version: str,
        ledger_sha256: str,
        scenarios: Mapping[str, bool],
    ) -> Stage5ForkEvidence:
        if not campaign_id:
            raise ValueError("campaign_id is required")
        if market not in MARKETS:
            raise ValueError(f"unsupported Prediction market: {market}")
        if chain_id <= 0:
            raise ValueError("chain_id must be positive")
        if fork_block_number <= 0:
            raise ValueError("fork_block_number must be positive")
        if not anvil_version.strip():
            raise ValueError("anvil_version is required")
        provisional = cls(
            origin=origin,
            source_sha=_validate_source_sha(source_sha),
            recorded_at=_validate_recorded_at(recorded_at),
            campaign_id=campaign_id,
            market=market,
            chain_id=chain_id,
            fork_block_number=fork_block_number,
            fork_block_hash=_validate_block_hash(fork_block_hash),
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
            chain_id=provisional.chain_id,
            fork_block_number=provisional.fork_block_number,
            fork_block_hash=provisional.fork_block_hash,
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
            chain_id=int(obj["chain_id"]),
            fork_block_number=int(obj["fork_block_number"]),
            fork_block_hash=str(obj["fork_block_hash"]),
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


def _finalized_bet_counts(path: Path, *, market: str) -> tuple[int, int]:
    target = MARKETS[market].address.lower()
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute(
            "SELECT target,calldata,value_wei,state FROM execution_intents ORDER BY id"
        ).fetchall()
    bull = 0
    bear = 0
    for target_raw, calldata_raw, value_wei_raw, state_raw in rows:
        if IntentState(str(state_raw)) != IntentState.FINALIZED:
            continue
        if str(target_raw).lower() != target:
            continue
        if int(value_wei_raw) <= 0:
            continue
        calldata = str(calldata_raw).lower()
        if len(calldata) != BET_CALLDATA_HEX_LENGTH:
            continue
        if calldata.startswith(BULL_SELECTOR):
            bull += 1
        elif calldata.startswith(BEAR_SELECTOR):
            bear += 1
    return bull, bear


def evaluate_stage5b_fork_gate(
    *,
    ledger_path: Path,
    evidence: Stage5ForkEvidence,
    expected_source_sha: str | None = None,
) -> Stage5ForkGateReport:
    report = build_execution_intent_report(ledger_path)
    bull, bear = _finalized_bet_counts(ledger_path, market=evidence.market)
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
            blockers.append(f"scenario_not_observed:{scenario}")

    return Stage5ForkGateReport(
        ready=not blockers,
        blockers=tuple(blockers),
        total_intents=report.total,
        unresolved_intents=report.unresolved,
        finalized_bull=bull,
        finalized_bear=bear,
    )