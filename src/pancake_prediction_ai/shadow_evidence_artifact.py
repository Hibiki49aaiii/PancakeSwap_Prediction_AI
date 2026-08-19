from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .event_store import EventStore, StoredEvent
from .shadow_economic_summary import ShadowEconomicSummary, summarize_shadow_economics


SHADOW_EVIDENCE_ARTIFACT_SCHEMA = "shadow_economic_evidence_v1"


@dataclass(frozen=True, slots=True)
class ShadowEconomicEvidenceArtifact:
    artifact_sha256: str
    payload: Mapping[str, Any]

    def validate(self) -> None:
        if len(self.artifact_sha256) != 64:
            raise ValueError("artifact_sha256 must be 64 hex characters")
        try:
            int(self.artifact_sha256, 16)
        except ValueError as exc:
            raise ValueError("artifact_sha256 must be hex") from exc
        if self.payload.get("schema") != SHADOW_EVIDENCE_ARTIFACT_SCHEMA:
            raise ValueError("unsupported shadow economic evidence artifact schema")
        expected = hashlib.sha256(_canonical(self.payload)).hexdigest()
        if expected != self.artifact_sha256:
            raise ValueError("shadow economic evidence artifact SHA-256 mismatch")
        _validate_payload(self.payload)

    def write(self, path: str | Path) -> Path:
        self.validate()
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            {"artifact_sha256": self.artifact_sha256, "payload": self.payload},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination


def _canonical(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("shadow evidence payload must be canonical JSON serializable") from exc


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a 64-character hash")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be hexadecimal") from exc
    return value


def _int(payload: Mapping[str, Any], field: str) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be integer")
    return value


def _numeric(payload: Mapping[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def _summary_payload(summary: ShadowEconomicSummary) -> dict[str, Any]:
    raw = asdict(summary)
    return {key: raw[key] for key in raw}


def _event_maps(events: tuple[StoredEvent, ...]) -> tuple[dict[str, StoredEvent], dict[str, StoredEvent]]:
    by_id: dict[str, StoredEvent] = {}
    by_hash: dict[str, StoredEvent] = {}
    for stored in events:
        event_id = stored.event.event_id
        if event_id in by_id:
            raise ValueError(f"duplicate Event Store event_id: {event_id}")
        if stored.event_hash in by_hash:
            raise ValueError("duplicate Event Store event_hash")
        by_id[event_id] = stored
        by_hash[stored.event_hash] = stored
    return by_id, by_hash


def _assumption_profile(decision: StoredEvent) -> dict[str, Any]:
    payload = decision.event.payload
    assumed = payload.get("assumed_execution")
    if not isinstance(assumed, dict):
        raise ValueError("shadow economic decision lacks assumed_execution")
    profile = {
        "stake_wei": _int(payload, "stake_wei"),
        "gas_cost_wei": _int(assumed, "gas_cost_wei"),
        "same_side_inflow_wei": _int(assumed, "same_side_inflow_wei"),
        "opposite_side_inflow_wei": _int(assumed, "opposite_side_inflow_wei"),
        "execution_success_probability": _numeric(
            assumed, "execution_success_probability"
        ),
        "min_expected_return": _numeric(assumed, "min_expected_return"),
    }
    if profile["stake_wei"] <= 0:
        raise ValueError("shadow economic evidence contains non-positive paper stake")
    if profile["gas_cost_wei"] < 0:
        raise ValueError("shadow economic evidence contains negative assumed gas")
    if not 0.0 <= profile["execution_success_probability"] <= 1.0:
        raise ValueError("shadow economic evidence execution probability is invalid")
    return profile


def _round_rows(events: tuple[StoredEvent, ...]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    by_id, by_hash = _event_maps(events)
    decisions: dict[int, StoredEvent] = {}
    settlements: dict[int, StoredEvent] = {}
    for stored in events:
        event = stored.event
        if event.source != "shadow":
            continue
        if event.topic not in {"shadow.economic_decision", "shadow.economic_settlement"}:
            continue
        round_id = event.payload.get("round_id")
        if isinstance(round_id, bool) or not isinstance(round_id, int) or round_id < 0:
            raise ValueError("shadow economic event round_id is invalid")
        target = decisions if event.topic == "shadow.economic_decision" else settlements
        if round_id in target:
            raise ValueError(f"duplicate {event.topic} for round {round_id}")
        target[round_id] = stored

    orphaned = set(settlements) - set(decisions)
    if orphaned:
        raise ValueError(f"shadow settlements without decisions: {sorted(orphaned)}")

    rows: list[dict[str, Any]] = []
    model_hashes: set[str] = set()
    assumption_hashes: set[str] = set()
    for round_id in sorted(decisions):
        decision = decisions[round_id]
        payload = decision.event.payload
        model_artifact_sha = _hash(
            payload.get("promoted_model_artifact_sha256"),
            "promoted_model_artifact_sha256",
        )
        model_hashes.add(model_artifact_sha)
        model_decision_event_id = payload.get("model_decision_event_id")
        if not isinstance(model_decision_event_id, str) or not model_decision_event_id:
            raise ValueError("shadow economic decision lacks model_decision_event_id")
        model_decision = by_id.get(model_decision_event_id)
        if model_decision is None:
            raise ValueError(
                f"shadow economic decision references missing model decision {model_decision_event_id}"
            )
        if (
            model_decision.event.source != "shadow"
            or model_decision.event.topic != "shadow.model_decision"
        ):
            raise ValueError("economic decision model reference is not a model decision event")
        if model_decision.ingest_seq >= decision.ingest_seq:
            raise ValueError("economic decision predates its referenced model decision")

        source_tip_hash = _hash(
            payload.get("source_snapshot_tip_hash"), "source_snapshot_tip_hash"
        )
        source_tip = by_hash.get(source_tip_hash)
        if source_tip is None:
            raise ValueError("economic decision source snapshot tip is absent from Event Store")
        if source_tip.ingest_seq >= model_decision.ingest_seq:
            raise ValueError("model decision source tip must precede model decision event")

        source_round_event_id = payload.get("source_round_snapshot_event_id")
        if not isinstance(source_round_event_id, str) or not source_round_event_id:
            raise ValueError("economic decision lacks source_round_snapshot_event_id")
        source_round = by_id.get(source_round_event_id)
        if source_round is None:
            raise ValueError("economic decision source round snapshot is missing")
        if source_round.ingest_seq > source_tip.ingest_seq:
            raise ValueError("source round snapshot was unavailable at model source tip")

        profile = _assumption_profile(decision)
        assumption_sha = _sha256_payload(profile)
        assumption_hashes.add(assumption_sha)
        row: dict[str, Any] = {
            "round_id": round_id,
            "action": str(payload.get("action")),
            "selected_side": payload.get("selected_side"),
            "promoted_model_artifact_sha256": model_artifact_sha,
            "model_decision": {
                "event_id": model_decision.event.event_id,
                "event_hash": model_decision.event_hash,
                "ingest_seq": model_decision.ingest_seq,
            },
            "economic_decision": {
                "event_id": decision.event.event_id,
                "event_hash": decision.event_hash,
                "ingest_seq": decision.ingest_seq,
                "observed_at_ns": decision.event.observed_at_ns,
            },
            "model_source_snapshot_tip_hash": source_tip_hash,
            "source_round_snapshot": {
                "event_id": source_round.event.event_id,
                "event_hash": source_round.event_hash,
                "ingest_seq": source_round.ingest_seq,
            },
            "execution_assumption_profile_sha256": assumption_sha,
            "execution_assumption_profile": profile,
            "settlement": None,
        }

        settlement = settlements.get(round_id)
        if settlement is not None:
            settlement_payload = settlement.event.payload
            if settlement_payload.get("economic_decision_event_id") != decision.event.event_id:
                raise ValueError("settlement references a different economic decision")
            snapshot_event_id = settlement_payload.get("settlement_snapshot_event_id")
            if not isinstance(snapshot_event_id, str) or not snapshot_event_id:
                raise ValueError("settlement lacks settlement_snapshot_event_id")
            snapshot = by_id.get(snapshot_event_id)
            if snapshot is None:
                raise ValueError("settlement source snapshot is missing")
            if (
                snapshot.event.source != "pancake_prediction"
                or snapshot.event.topic != "prediction.settlement_snapshot"
            ):
                raise ValueError("settlement source event is not a settlement snapshot")
            if settlement.prev_hash != snapshot.event_hash:
                raise ValueError("settlement is not directly chained to its source snapshot")
            if snapshot.ingest_seq >= settlement.ingest_seq:
                raise ValueError("settlement snapshot does not precede settlement")
            row["settlement"] = {
                "resolution": str(settlement_payload.get("resolution")),
                "snapshot_event_id": snapshot.event.event_id,
                "snapshot_event_hash": snapshot.event_hash,
                "snapshot_ingest_seq": snapshot.ingest_seq,
                "settlement_event_id": settlement.event.event_id,
                "settlement_event_hash": settlement.event_hash,
                "settlement_ingest_seq": settlement.ingest_seq,
                "block_number": _int(settlement_payload, "block_number"),
                "block_hash": str(settlement_payload.get("block_hash")),
                "pnl_if_executed_wei": _int(
                    settlement_payload, "pnl_if_executed_wei"
                ),
                "probability_adjusted_pnl_wei": _numeric(
                    settlement_payload, "probability_adjusted_pnl_wei"
                ),
                "claim_or_refund_gas_modeled": bool(
                    settlement_payload.get("claim_or_refund_gas_modeled") is True
                ),
            }
        rows.append(row)

    return rows, sorted(model_hashes), sorted(assumption_hashes)


def build_shadow_economic_evidence_artifact(
    store: EventStore,
    *,
    generated_at_ns: int,
) -> ShadowEconomicEvidenceArtifact:
    """Freeze one observed-store shadow record without calling it live PnL.

    On-chain outcomes and final pools are observed. Bets, execution success,
    decision gas and stake are paper/simulation inputs. The artifact preserves
    that mixed evidence class explicitly and must not be used as proof of funded
    live profitability.
    """

    if store.mode != "observed":
        raise ValueError("shadow economic evidence artifact requires observed Event Store")
    if generated_at_ns < 0:
        raise ValueError("generated_at_ns must be non-negative")
    if not store.verify_chain():
        raise ValueError("source observed Event Store hash chain verification failed")
    events = store.read_all_ingest_order()
    if not events:
        raise ValueError("shadow economic evidence artifact requires non-empty Event Store")
    rows, model_hashes, assumption_hashes = _round_rows(events)
    if not rows:
        raise ValueError("shadow economic evidence artifact requires economic decisions")
    summary = summarize_shadow_economics(store)
    settled_rounds = sum(row["settlement"] is not None for row in rows)
    unresolved_rounds = len(rows) - settled_rounds

    payload: dict[str, Any] = {
        "schema": SHADOW_EVIDENCE_ARTIFACT_SCHEMA,
        "generated_at_ns": generated_at_ns,
        "evidence_classification": {
            "artifact_class": "hybrid_shadow_not_live",
            "event_store_availability": "observed",
            "market_and_protocol_inputs": "observed",
            "settlement_outcome_and_final_pool": "observed_onchain_when_settled",
            "model_output": "derived",
            "paper_action_and_stake": "simulated",
            "decision_gas_cost": "assumed",
            "execution_success_probability": "assumed",
            "paper_execution": "simulated_not_broadcast",
            "claim_or_refund_gas": "not_modeled",
            "funded_live_profitability_evidence": False,
        },
        "source_event_store": {
            "availability_mode": store.mode,
            "hash_chain_verified": True,
            "event_count": len(events),
            "tip_hash": events[-1].event_hash,
        },
        "promoted_model_artifact_sha256_set": model_hashes,
        "execution_assumption_profile_sha256_set": assumption_hashes,
        "rounds": rows,
        "summary": _summary_payload(summary),
        "completeness": {
            "economic_decision_rounds": len(rows),
            "settled_rounds": settled_rounds,
            "unresolved_rounds": unresolved_rounds,
            "all_decisions_settled": unresolved_rounds == 0,
            "has_any_settled_round": settled_rounds > 0,
            "fully_costed_claim_or_refund_gas": summary.claim_or_refund_gas_fully_modeled,
        },
        "claims": {
            "may_support_shadow_model_evaluation": settled_rounds > 0,
            "may_support_funded_live_profitability_claim": False,
            "may_clear_stage_6b_funded_validation": False,
        },
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    artifact = ShadowEconomicEvidenceArtifact(digest, payload)
    artifact.validate()
    return artifact


def _validate_payload(payload: Mapping[str, Any]) -> None:
    classification = payload.get("evidence_classification")
    if not isinstance(classification, dict):
        raise ValueError("shadow evidence classification is invalid")
    if classification.get("artifact_class") != "hybrid_shadow_not_live":
        raise ValueError("shadow evidence artifact class must remain hybrid_shadow_not_live")
    if classification.get("event_store_availability") != "observed":
        raise ValueError("shadow evidence source availability must be observed")
    if classification.get("funded_live_profitability_evidence") is not False:
        raise ValueError("shadow evidence cannot claim funded live profitability")
    source = payload.get("source_event_store")
    if not isinstance(source, dict) or source.get("availability_mode") != "observed":
        raise ValueError("shadow evidence source store must be observed")
    if source.get("hash_chain_verified") is not True:
        raise ValueError("shadow evidence source hash chain must be verified")
    _hash(source.get("tip_hash"), "source_event_store.tip_hash")
    if _int(source, "event_count") <= 0:
        raise ValueError("shadow evidence source event_count must be positive")

    model_hashes = payload.get("promoted_model_artifact_sha256_set")
    assumption_hashes = payload.get("execution_assumption_profile_sha256_set")
    if not isinstance(model_hashes, list) or not model_hashes:
        raise ValueError("shadow evidence requires promoted model artifact hashes")
    if not isinstance(assumption_hashes, list) or not assumption_hashes:
        raise ValueError("shadow evidence requires execution assumption profile hashes")
    if model_hashes != sorted(set(model_hashes)):
        raise ValueError("shadow evidence promoted model hash set is not canonical")
    if assumption_hashes != sorted(set(assumption_hashes)):
        raise ValueError("shadow evidence assumption hash set is not canonical")
    for value in model_hashes:
        _hash(value, "promoted_model_artifact_sha256_set")
    for value in assumption_hashes:
        _hash(value, "execution_assumption_profile_sha256_set")

    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("shadow evidence rounds are invalid")
    round_ids: list[int] = []
    settled = 0
    for row in rounds:
        if not isinstance(row, dict):
            raise ValueError("shadow evidence round row is invalid")
        round_id = _int(row, "round_id")
        round_ids.append(round_id)
        _hash(row.get("promoted_model_artifact_sha256"), "round model artifact hash")
        profile = row.get("execution_assumption_profile")
        if not isinstance(profile, dict):
            raise ValueError("shadow evidence assumption profile is invalid")
        profile_hash = _hash(
            row.get("execution_assumption_profile_sha256"),
            "execution_assumption_profile_sha256",
        )
        if _sha256_payload(profile) != profile_hash:
            raise ValueError("shadow evidence assumption profile SHA-256 mismatch")
        for key in ("model_decision", "economic_decision", "source_round_snapshot"):
            reference = row.get(key)
            if not isinstance(reference, dict):
                raise ValueError(f"shadow evidence {key} reference is invalid")
            _hash(reference.get("event_hash"), f"{key}.event_hash")
            if _int(reference, "ingest_seq") <= 0:
                raise ValueError(f"shadow evidence {key}.ingest_seq must be positive")
        _hash(row.get("model_source_snapshot_tip_hash"), "model source snapshot tip")
        settlement = row.get("settlement")
        if settlement is not None:
            if not isinstance(settlement, dict):
                raise ValueError("shadow evidence settlement row is invalid")
            settled += 1
            _hash(settlement.get("snapshot_event_hash"), "settlement snapshot hash")
            _hash(settlement.get("settlement_event_hash"), "settlement event hash")
            if _int(settlement, "snapshot_ingest_seq") >= _int(
                settlement, "settlement_ingest_seq"
            ):
                raise ValueError("shadow evidence settlement ordering is invalid")
    if round_ids != sorted(set(round_ids)):
        raise ValueError("shadow evidence round IDs must be unique and sorted")

    summary = payload.get("summary")
    completeness = payload.get("completeness")
    claims = payload.get("claims")
    if not isinstance(summary, dict) or not isinstance(completeness, dict):
        raise ValueError("shadow evidence summary/completeness is invalid")
    if _int(summary, "decision_rounds") != len(rounds):
        raise ValueError("shadow evidence summary decision count mismatch")
    if _int(summary, "settled_rounds") != settled:
        raise ValueError("shadow evidence summary settled count mismatch")
    if _int(completeness, "economic_decision_rounds") != len(rounds):
        raise ValueError("shadow evidence completeness decision count mismatch")
    if _int(completeness, "settled_rounds") != settled:
        raise ValueError("shadow evidence completeness settled count mismatch")
    if _int(completeness, "unresolved_rounds") != len(rounds) - settled:
        raise ValueError("shadow evidence unresolved count mismatch")
    if not isinstance(claims, dict):
        raise ValueError("shadow evidence claims are invalid")
    if claims.get("may_support_funded_live_profitability_claim") is not False:
        raise ValueError("shadow evidence cannot support funded live profitability claim")
    if claims.get("may_clear_stage_6b_funded_validation") is not False:
        raise ValueError("shadow evidence cannot clear Stage 6B")


def load_shadow_economic_evidence_artifact(
    path: str | Path,
) -> ShadowEconomicEvidenceArtifact:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("shadow economic evidence artifact could not be read") from exc
    if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
        raise ValueError("shadow economic evidence artifact document is invalid")
    artifact = ShadowEconomicEvidenceArtifact(
        artifact_sha256=str(document.get("artifact_sha256", "")),
        payload=document["payload"],
    )
    artifact.validate()
    return artifact
