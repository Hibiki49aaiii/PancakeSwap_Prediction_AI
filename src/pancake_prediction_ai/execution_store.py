from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path

from .execution_state import ExecutionIntent, IntentState


class IntentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_intents (
                intent_id TEXT PRIMARY KEY,
                round_id INTEGER NOT NULL,
                side TEXT NOT NULL CHECK(side IN ('BULL', 'BEAR')),
                amount_wei INTEGER NOT NULL CHECK(amount_wei > 0),
                nonce INTEGER,
                tx_hash TEXT,
                replacement_tx_hash TEXT,
                state TEXT NOT NULL,
                observed_block INTEGER,
                confirmations INTEGER NOT NULL DEFAULT 0 CHECK(confirmations >= 0)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_nonce
                ON execution_intents(nonce)
                WHERE nonce IS NOT NULL
                  AND state NOT IN ('finalized', 'cancelled', 'failed');

            CREATE INDEX IF NOT EXISTS ix_execution_state
                ON execution_intents(state);
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "IntentStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def save(self, intent: ExecutionIntent) -> None:
        intent.validate()
        values = asdict(intent)
        values["state"] = intent.state.value
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO execution_intents (
                    intent_id, round_id, side, amount_wei, nonce, tx_hash,
                    replacement_tx_hash, state, observed_block, confirmations
                ) VALUES (
                    :intent_id, :round_id, :side, :amount_wei, :nonce, :tx_hash,
                    :replacement_tx_hash, :state, :observed_block, :confirmations
                )
                ON CONFLICT(intent_id) DO UPDATE SET
                    round_id=excluded.round_id,
                    side=excluded.side,
                    amount_wei=excluded.amount_wei,
                    nonce=excluded.nonce,
                    tx_hash=excluded.tx_hash,
                    replacement_tx_hash=excluded.replacement_tx_hash,
                    state=excluded.state,
                    observed_block=excluded.observed_block,
                    confirmations=excluded.confirmations
                """,
                values,
            )

    def get(self, intent_id: str) -> ExecutionIntent | None:
        row = self._conn.execute(
            "SELECT * FROM execution_intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        return self._row_to_intent(row) if row else None

    def unresolved(self) -> list[ExecutionIntent]:
        terminal = tuple(state.value for state in (IntentState.FINALIZED, IntentState.CANCELLED, IntentState.FAILED))
        rows = self._conn.execute(
            """
            SELECT * FROM execution_intents
            WHERE state NOT IN (?, ?, ?)
            ORDER BY round_id, intent_id
            """,
            terminal,
        ).fetchall()
        return [self._row_to_intent(row) for row in rows]

    def count_unresolved(self) -> int:
        return len(self.unresolved())

    @staticmethod
    def _row_to_intent(row: sqlite3.Row) -> ExecutionIntent:
        intent = ExecutionIntent(
            intent_id=row["intent_id"],
            round_id=row["round_id"],
            side=row["side"],
            amount_wei=row["amount_wei"],
            nonce=row["nonce"],
            tx_hash=row["tx_hash"],
            replacement_tx_hash=row["replacement_tx_hash"],
            state=IntentState(row["state"]),
            observed_block=row["observed_block"],
            confirmations=row["confirmations"],
        )
        intent.validate()
        return intent
