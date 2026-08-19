from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .evidence_gate import Evidence, EvidenceKind, EvidenceOrigin
from .execution_state import ExecutionIntent, IntentState, reconcile_receipt
from .execution_store import IntentStore
from .runtime_fingerprint import (
    RuntimeFingerprint,
    capture_runtime_fingerprint,
    validate_runtime_fingerprint_payload,
)


STAGE5A_DRILL_SCHEMA = "stage5a_execution_drill_v2"


@dataclass(frozen=True, slots=True)
class Stage5ADrillResult:
    runtime_fingerprint: RuntimeFingerprint
    journal_mode_wal: bool
    synchronous_full: bool
    unresolved_recovered_after_restart: bool
    duplicate_active_nonce_rejected: bool
    unknown_state_persisted_after_missing_receipt: bool
    finalized_state_persisted_after_confirmations: bool
    terminal_nonce_released: bool
    terminal_reuse_cleanup_persisted: bool
    unresolved_count_final: int
    required_confirmations: int

    @property
    def passed(self) -> bool:
        return (
            validate_runtime_fingerprint_payload(self.runtime_fingerprint.payload())
            and self.journal_mode_wal
            and self.synchronous_full
            and self.unresolved_recovered_after_restart
            and self.duplicate_active_nonce_rejected
            and self.unknown_state_persisted_after_missing_receipt
            and self.finalized_state_persisted_after_confirmations
            and self.terminal_nonce_released
            and self.terminal_reuse_cleanup_persisted
            and self.unresolved_count_final == 0
            and self.required_confirmations >= 1
        )


def _tx_hash(byte: str) -> str:
    return "0x" + byte * 64


def _pragma_snapshot(store: IntentStore) -> tuple[bool, bool]:
    journal = str(store._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    synchronous = int(store._conn.execute("PRAGMA synchronous").fetchone()[0])
    return journal == "wal", synchronous == 2


def run_stage5a_execution_drill(
    path: str | Path,
    *,
    required_confirmations: int = 3,
) -> Stage5ADrillResult:
    """Exercise the durable intent fault model against a fresh local SQLite DB.

    The drill never creates or submits a blockchain transaction. Transaction
    hashes and nonces are synthetic fixtures used only to test persistence,
    restart recovery, nonce exclusion and receipt-state reconciliation.
    """

    if required_confirmations < 1:
        raise ValueError("required_confirmations must be >= 1")
    destination = Path(path)
    if destination.exists():
        raise ValueError("Stage 5A drill database path must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    runtime_fingerprint = capture_runtime_fingerprint()

    intent_id = "stage5a-drill-primary"
    reuse_id = "stage5a-drill-reuse"
    nonce = 17
    submitted = ExecutionIntent(
        intent_id=intent_id,
        round_id=1,
        side="BULL",
        amount_wei=1,
        nonce=nonce,
        tx_hash=_tx_hash("1"),
        state=IntentState.SUBMITTED,
    )

    with IntentStore(destination) as store:
        journal_mode_wal, synchronous_full = _pragma_snapshot(store)
        store.save(submitted)

    with IntentStore(destination) as store:
        recovered = store.get(intent_id)
        unresolved_recovered = (
            recovered is not None
            and recovered.state is IntentState.SUBMITTED
            and store.count_unresolved() == 1
        )
        duplicate_active_nonce_rejected = False
        duplicate = ExecutionIntent(
            intent_id="stage5a-drill-duplicate",
            round_id=2,
            side="BEAR",
            amount_wei=1,
            nonce=nonce,
            state=IntentState.RESERVED,
        )
        try:
            store.save(duplicate)
        except sqlite3.IntegrityError:
            duplicate_active_nonce_rejected = True
        current = store.get(intent_id)
        if current is None:
            raise AssertionError("Stage 5A primary intent disappeared after restart")
        unknown = reconcile_receipt(
            current,
            receipt_present=False,
            canonical_block=False,
            block_number=None,
            confirmations=0,
            required_confirmations=required_confirmations,
        )
        store.save(unknown)

    with IntentStore(destination) as store:
        recovered_unknown = store.get(intent_id)
        unknown_persisted = (
            recovered_unknown is not None
            and recovered_unknown.state is IntentState.UNKNOWN
            and recovered_unknown.observed_block is None
            and recovered_unknown.confirmations == 0
        )
        if recovered_unknown is None:
            raise AssertionError("Stage 5A UNKNOWN intent disappeared after restart")
        finalized = reconcile_receipt(
            recovered_unknown,
            receipt_present=True,
            canonical_block=True,
            block_number=123,
            confirmations=required_confirmations,
            required_confirmations=required_confirmations,
        )
        store.save(finalized)

    with IntentStore(destination) as store:
        recovered_finalized = store.get(intent_id)
        finalized_persisted = (
            recovered_finalized is not None
            and recovered_finalized.state is IntentState.FINALIZED
            and recovered_finalized.observed_block == 123
            and recovered_finalized.confirmations == required_confirmations
            and store.count_unresolved() == 0
        )
        reuse = ExecutionIntent(
            intent_id=reuse_id,
            round_id=3,
            side="BEAR",
            amount_wei=1,
            nonce=nonce,
            state=IntentState.RESERVED,
        )
        terminal_nonce_released = True
        try:
            store.save(reuse)
        except sqlite3.IntegrityError:
            terminal_nonce_released = False
        if terminal_nonce_released:
            persisted_reuse = store.get(reuse_id)
            if persisted_reuse is None:
                raise AssertionError("Stage 5A nonce-reuse intent did not persist")
            store.save(persisted_reuse.transition(IntentState.CANCELLED))

    with IntentStore(destination) as store:
        cleanup = store.get(reuse_id)
        cleanup_persisted = (
            terminal_nonce_released
            and cleanup is not None
            and cleanup.state is IntentState.CANCELLED
        )
        unresolved_final = store.count_unresolved()

    return Stage5ADrillResult(
        runtime_fingerprint=runtime_fingerprint,
        journal_mode_wal=journal_mode_wal,
        synchronous_full=synchronous_full,
        unresolved_recovered_after_restart=unresolved_recovered,
        duplicate_active_nonce_rejected=duplicate_active_nonce_rejected,
        unknown_state_persisted_after_missing_receipt=unknown_persisted,
        finalized_state_persisted_after_confirmations=finalized_persisted,
        terminal_nonce_released=terminal_nonce_released,
        terminal_reuse_cleanup_persisted=cleanup_persisted,
        unresolved_count_final=unresolved_final,
        required_confirmations=required_confirmations,
    )


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def make_stage5a_evidence(
    result: Stage5ADrillResult,
    *,
    recorded_at: str | None = None,
) -> Evidence:
    result_payload = asdict(result)
    result_payload.pop("runtime_fingerprint", None)
    runtime_payload = result.runtime_fingerprint.payload()
    payload: dict[str, Any] = {
        "schema": STAGE5A_DRILL_SCHEMA,
        "drill_type": "local_sqlite_execution_state_durability",
        "blockchain_transaction_created": False,
        "transaction_signed": False,
        "transaction_broadcast": False,
        "runtime_fingerprint": runtime_payload,
        "runtime_fingerprint_sha256": result.runtime_fingerprint.sha256,
        **result_payload,
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return Evidence(
        kind=EvidenceKind.STAGE5A_DRILL,
        origin=EvidenceOrigin.OBSERVED,
        passed=result.passed,
        artifact_sha256=digest,
        recorded_at=recorded_at or datetime.now(timezone.utc).isoformat(),
        payload=payload,
    )


def write_stage5a_evidence(evidence: Evidence, path: str | Path) -> Path:
    if evidence.kind is not EvidenceKind.STAGE5A_DRILL:
        raise ValueError("Stage 5A writer requires STAGE5A_DRILL evidence")
    if evidence.origin is not EvidenceOrigin.OBSERVED:
        raise ValueError("Stage 5A writer requires OBSERVED evidence")
    payload = dict(evidence.payload)
    if hashlib.sha256(_canonical(payload)).hexdigest() != evidence.artifact_sha256:
        raise ValueError("Stage 5A evidence SHA-256 mismatch")
    document = {
        "kind": evidence.kind.value,
        "origin": evidence.origin.value,
        "passed": evidence.passed,
        "artifact_sha256": evidence.artifact_sha256,
        "recorded_at": evidence.recorded_at,
        "payload": payload,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
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
