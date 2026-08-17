from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from .rpc import RpcError, RpcResponseError


class IntentState(StrEnum):
    CREATED = "created"
    RESERVED = "reserved"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    UNKNOWN = "unknown"
    RETRYABLE = "retryable"
    MINED = "mined"
    REORGED = "reorged"
    CONSUMED_UNKNOWN = "consumed_unknown"
    FINALIZED = "finalized"
    FAILED = "failed"


TERMINAL_STATES = {IntentState.CONSUMED_UNKNOWN, IntentState.FINALIZED, IntentState.FAILED}
RESERVABLE_STATES = {IntentState.CREATED, IntentState.RETRYABLE, IntentState.RESERVED}
AMBIGUOUS_SUBMISSION_MARKERS = (
    "already known",
    "known transaction",
    "nonce too low",
    "replacement transaction underpriced",
)


class ForkExecutionRpc(Protocol):
    @property
    def fork_only(self) -> bool: ...

    def transaction_count(self, address: str, tag: str = "pending") -> int: ...

    def transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None: ...

    def transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None: ...

    def block_number(self) -> int: ...

    def block(self, number: int) -> dict[str, Any]: ...

    def send_transaction(
        self,
        *,
        from_address: str,
        to: str,
        data: str,
        value_wei: int,
        gas: int | None = None,
        gas_price_wei: int | None = None,
        nonce: int | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    id: int
    idempotency_key: str
    sender: str
    target: str
    calldata: str
    value_wei: int
    nonce: int | None
    state: IntentState
    current_tx_hash: str | None
    attempts: int
    receipt_block_number: int | None
    receipt_block_hash: str | None
    last_error: str | None


SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT NOT NULL UNIQUE,
  sender TEXT NOT NULL,
  target TEXT NOT NULL,
  calldata TEXT NOT NULL,
  value_wei INTEGER NOT NULL CHECK (value_wei >= 0),
  nonce INTEGER,
  state TEXT NOT NULL,
  current_tx_hash TEXT,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  receipt_block_number INTEGER,
  receipt_block_hash TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_sender_nonce
  ON execution_intents(sender, nonce) WHERE nonce IS NOT NULL;

CREATE TABLE IF NOT EXISTS execution_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  intent_id INTEGER NOT NULL,
  attempt_number INTEGER NOT NULL,
  outcome TEXT NOT NULL,
  tx_hash TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(intent_id, attempt_number),
  FOREIGN KEY(intent_id) REFERENCES execution_intents(id)
);
"""


def _normalize_address(value: str) -> str:
    text = value.lower()
    if not text.startswith("0x") or len(text) != 42:
        raise ValueError("address must be a 20-byte hex address")
    try:
        int(text[2:], 16)
    except ValueError as exc:
        raise ValueError("address must be hexadecimal") from exc
    return text


def _hex_int(value: object) -> int:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _is_ambiguous_submission_error(exc: RpcResponseError) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in AMBIGUOUS_SUBMISSION_MARKERS)


@dataclass(slots=True)
class ExecutionIntentStore:
    path: Path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    @staticmethod
    def _row_to_intent(row: sqlite3.Row) -> ExecutionIntent:
        return ExecutionIntent(
            id=int(row["id"]),
            idempotency_key=str(row["idempotency_key"]),
            sender=str(row["sender"]),
            target=str(row["target"]),
            calldata=str(row["calldata"]),
            value_wei=int(row["value_wei"]),
            nonce=None if row["nonce"] is None else int(row["nonce"]),
            state=IntentState(str(row["state"])),
            current_tx_hash=(
                None if row["current_tx_hash"] is None else str(row["current_tx_hash"])
            ),
            attempts=int(row["attempts"]),
            receipt_block_number=(
                None if row["receipt_block_number"] is None else int(row["receipt_block_number"])
            ),
            receipt_block_hash=(
                None if row["receipt_block_hash"] is None else str(row["receipt_block_hash"])
            ),
            last_error=None if row["last_error"] is None else str(row["last_error"]),
        )

    def get(self, intent_id: int) -> ExecutionIntent:
        with closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM execution_intents WHERE id=?", (intent_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"execution intent not found: {intent_id}")
        return self._row_to_intent(cast(sqlite3.Row, row))

    def get_or_create(
        self,
        *,
        idempotency_key: str,
        sender: str,
        target: str,
        calldata: str,
        value_wei: int,
    ) -> ExecutionIntent:
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if value_wei < 0:
            raise ValueError("value_wei must be non-negative")
        sender_normalized = _normalize_address(sender)
        target_normalized = _normalize_address(target)
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM execution_intents WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO execution_intents(
                      idempotency_key,sender,target,calldata,value_wei,state
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        idempotency_key,
                        sender_normalized,
                        target_normalized,
                        calldata,
                        value_wei,
                        IntentState.CREATED.value,
                    ),
                )
                intent_id = cursor.lastrowid
                if intent_id is None:
                    raise RuntimeError("SQLite did not return an execution intent id")
                row = conn.execute(
                    "SELECT * FROM execution_intents WHERE id=?", (intent_id,)
                ).fetchone()
            conn.commit()
        if row is None:
            raise RuntimeError("execution intent disappeared during creation")
        intent = self._row_to_intent(cast(sqlite3.Row, row))
        expected = (sender_normalized, target_normalized, calldata, value_wei)
        actual = (intent.sender, intent.target, intent.calldata, intent.value_wei)
        if actual != expected:
            raise ValueError("idempotency_key already exists with a different payload")
        return intent

    def reserve_nonce(self, intent_id: int, pending_nonce: int) -> ExecutionIntent:
        if pending_nonce < 0:
            raise ValueError("pending_nonce must be non-negative")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM execution_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"execution intent not found: {intent_id}")
            intent = self._row_to_intent(cast(sqlite3.Row, row))
            if intent.state not in RESERVABLE_STATES:
                raise ValueError(f"cannot reserve nonce from state {intent.state}")
            if intent.nonce is not None:
                nonce = intent.nonce
            else:
                max_row = conn.execute(
                    "SELECT MAX(nonce) AS max_nonce FROM execution_intents WHERE sender=?",
                    (intent.sender,),
                ).fetchone()
                max_reserved = None if max_row is None else max_row["max_nonce"]
                nonce = pending_nonce
                if max_reserved is not None:
                    nonce = max(nonce, int(max_reserved) + 1)
            conn.execute(
                """
                UPDATE execution_intents
                SET nonce=?,state=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (nonce, IntentState.RESERVED.value, intent_id),
            )
            conn.commit()
        return self.get(intent_id)

    def begin_submission(self, intent_id: int) -> ExecutionIntent:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM execution_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"execution intent not found: {intent_id}")
            intent = self._row_to_intent(cast(sqlite3.Row, row))
            if intent.state != IntentState.RESERVED or intent.nonce is None:
                raise ValueError("intent must be reserved before submission")
            attempt_number = intent.attempts + 1
            conn.execute(
                """
                UPDATE execution_intents
                SET state=?,attempts=?,last_error=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (IntentState.SUBMITTING.value, attempt_number, intent_id),
            )
            conn.execute(
                """
                INSERT INTO execution_attempts(intent_id,attempt_number,outcome)
                VALUES(?,?,?)
                """,
                (intent_id, attempt_number, "started"),
            )
            conn.commit()
        return self.get(intent_id)

    def mark_submitted(self, intent_id: int, tx_hash: str) -> ExecutionIntent:
        return self._complete_attempt(
            intent_id,
            state=IntentState.SUBMITTED,
            outcome="submitted",
            tx_hash=tx_hash.lower(),
            error=None,
        )

    def mark_unknown(self, intent_id: int, error: str) -> ExecutionIntent:
        return self._complete_attempt(
            intent_id,
            state=IntentState.UNKNOWN,
            outcome="unknown",
            tx_hash=None,
            error=error,
        )

    def mark_failed(self, intent_id: int, error: str) -> ExecutionIntent:
        return self._complete_attempt(
            intent_id,
            state=IntentState.FAILED,
            outcome="rejected",
            tx_hash=None,
            error=error,
        )

    def _complete_attempt(
        self,
        intent_id: int,
        *,
        state: IntentState,
        outcome: str,
        tx_hash: str | None,
        error: str | None,
    ) -> ExecutionIntent:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts,state FROM execution_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"execution intent not found: {intent_id}")
            if IntentState(str(row["state"])) != IntentState.SUBMITTING:
                raise ValueError("intent is not awaiting a submission outcome")
            attempt_number = int(row["attempts"])
            conn.execute(
                """
                UPDATE execution_intents
                SET state=?,current_tx_hash=COALESCE(?,current_tx_hash),last_error=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (state.value, tx_hash, error, intent_id),
            )
            cursor = conn.execute(
                """
                UPDATE execution_attempts
                SET outcome=?,tx_hash=?,error=?,updated_at=CURRENT_TIMESTAMP
                WHERE intent_id=? AND attempt_number=? AND outcome='started'
                """,
                (outcome, tx_hash, error, intent_id, attempt_number),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("submission attempt journal is inconsistent")
            conn.commit()
        return self.get(intent_id)

    def recover_submitting(
        self,
        intent_id: int,
        *,
        state: IntentState,
        outcome: str,
        error: str,
    ) -> ExecutionIntent:
        if state not in {IntentState.RETRYABLE, IntentState.CONSUMED_UNKNOWN}:
            raise ValueError("invalid recovery state for interrupted submission")
        if outcome not in {"interrupted", "unknown"}:
            raise ValueError("invalid interrupted submission outcome")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT attempts,state FROM execution_intents WHERE id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"execution intent not found: {intent_id}")
            if IntentState(str(row["state"])) != IntentState.SUBMITTING:
                raise ValueError("intent is not an interrupted submission")
            attempt_number = int(row["attempts"])
            conn.execute(
                """
                UPDATE execution_intents
                SET state=?,last_error=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (state.value, error, intent_id),
            )
            cursor = conn.execute(
                """
                UPDATE execution_attempts
                SET outcome=?,error=?,updated_at=CURRENT_TIMESTAMP
                WHERE intent_id=? AND attempt_number=? AND outcome='started'
                """,
                (outcome, error, intent_id, attempt_number),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("interrupted submission journal is inconsistent")
            conn.commit()
        return self.get(intent_id)

    def set_reconciliation_state(
        self,
        intent_id: int,
        state: IntentState,
        *,
        receipt_block_number: int | None = None,
        receipt_block_hash: str | None = None,
        error: str | None = None,
    ) -> ExecutionIntent:
        if state in {IntentState.CREATED, IntentState.RESERVED, IntentState.SUBMITTING}:
            raise ValueError("invalid reconciliation state")
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE execution_intents
                SET state=?,receipt_block_number=?,receipt_block_hash=?,last_error=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (
                    state.value,
                    receipt_block_number,
                    None if receipt_block_hash is None else receipt_block_hash.lower(),
                    error,
                    intent_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"execution intent not found: {intent_id}")
            conn.commit()
        return self.get(intent_id)

    def attempts(self, intent_id: int) -> list[sqlite3.Row]:
        with closing(self.connect()) as conn:
            return conn.execute(
                """
                SELECT * FROM execution_attempts
                WHERE intent_id=? ORDER BY attempt_number
                """,
                (intent_id,),
            ).fetchall()


@dataclass(slots=True)
class ForkExecutionCoordinator:
    store: ExecutionIntentStore
    rpc: ForkExecutionRpc
    confirmations: int = 3

    def __post_init__(self) -> None:
        if not self.rpc.fork_only:
            raise ValueError("execution coordinator accepts fork-only RPC adapters")

    def submit(
        self,
        intent_id: int,
        *,
        gas: int | None = None,
        gas_price_wei: int | None = None,
    ) -> ExecutionIntent:
        intent = self.store.get(intent_id)
        if intent.state == IntentState.CREATED:
            pending_nonce = self.rpc.transaction_count(intent.sender, "pending")
            intent = self.store.reserve_nonce(intent.id, pending_nonce)
        elif intent.state == IntentState.RETRYABLE:
            if intent.nonce is None:
                raise RuntimeError("retryable intent lost its nonce reservation")
            intent = self.store.reserve_nonce(intent.id, intent.nonce)
        elif intent.state != IntentState.RESERVED:
            raise ValueError(f"refusing duplicate submission from state {intent.state}")

        intent = self.store.begin_submission(intent.id)
        if intent.nonce is None:
            raise RuntimeError("reserved intent has no nonce")
        try:
            tx_hash = self.rpc.send_transaction(
                from_address=intent.sender,
                to=intent.target,
                data=intent.calldata,
                value_wei=intent.value_wei,
                gas=gas,
                gas_price_wei=gas_price_wei,
                nonce=intent.nonce,
            )
        except RpcResponseError as exc:
            if _is_ambiguous_submission_error(exc):
                return self.store.mark_unknown(intent.id, str(exc))
            return self.store.mark_failed(intent.id, str(exc))
        except RpcError as exc:
            return self.store.mark_unknown(intent.id, str(exc))
        return self.store.mark_submitted(intent.id, tx_hash)

    def reconcile(self, intent_id: int) -> ExecutionIntent:
        if self.confirmations < 1:
            raise ValueError("confirmations must be positive")
        intent = self.store.get(intent_id)
        if intent.state in TERMINAL_STATES:
            return intent
        if intent.nonce is None:
            return intent

        if intent.state == IntentState.SUBMITTING:
            pending_nonce = self.rpc.transaction_count(intent.sender, "pending")
            if pending_nonce <= intent.nonce:
                return self.store.recover_submitting(
                    intent.id,
                    state=IntentState.RETRYABLE,
                    outcome="interrupted",
                    error="submission was interrupted before an outcome was durably recorded",
                )
            return self.store.recover_submitting(
                intent.id,
                state=IntentState.CONSUMED_UNKNOWN,
                outcome="unknown",
                error="submission was interrupted and its reserved nonce was consumed",
            )

        if intent.current_tx_hash is not None:
            receipt = self.rpc.transaction_receipt(intent.current_tx_hash)
            if receipt is not None:
                return self._reconcile_receipt(intent, receipt)
            pending_tx = self.rpc.transaction_by_hash(intent.current_tx_hash)
            if pending_tx is not None:
                return self.store.set_reconciliation_state(
                    intent.id,
                    IntentState.SUBMITTED,
                    error=None,
                )
            if intent.state in {IntentState.MINED, IntentState.REORGED}:
                self.store.set_reconciliation_state(
                    intent.id,
                    IntentState.REORGED,
                    error="previously mined transaction is no longer canonical",
                )

        pending_nonce = self.rpc.transaction_count(intent.sender, "pending")
        if pending_nonce <= intent.nonce:
            return self.store.set_reconciliation_state(
                intent.id,
                IntentState.RETRYABLE,
                error="reserved nonce is unconsumed; safe to retry the same nonce on local fork",
            )
        return self.store.set_reconciliation_state(
            intent.id,
            IntentState.CONSUMED_UNKNOWN,
            error="reserved nonce was consumed but the intent transaction cannot be identified",
        )

    def _reconcile_receipt(
        self,
        intent: ExecutionIntent,
        receipt: dict[str, Any],
    ) -> ExecutionIntent:
        status = _hex_int(receipt.get("status", "0x1"))
        block_number = _hex_int(receipt["blockNumber"])
        block_hash = str(receipt["blockHash"]).lower()
        canonical_block = self.rpc.block(block_number)
        canonical_hash = str(canonical_block["hash"]).lower()
        if canonical_hash != block_hash:
            return self.store.set_reconciliation_state(
                intent.id,
                IntentState.REORGED,
                receipt_block_number=block_number,
                receipt_block_hash=block_hash,
                error="receipt block is no longer canonical",
            )
        if status != 1:
            return self.store.set_reconciliation_state(
                intent.id,
                IntentState.FAILED,
                receipt_block_number=block_number,
                receipt_block_hash=block_hash,
                error="transaction reverted on local fork",
            )
        head = self.rpc.block_number()
        confirmations = head - block_number + 1
        state = IntentState.FINALIZED if confirmations >= self.confirmations else IntentState.MINED
        return self.store.set_reconciliation_state(
            intent.id,
            state,
            receipt_block_number=block_number,
            receipt_block_hash=block_hash,
            error=None,
        )
