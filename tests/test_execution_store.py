from __future__ import annotations

import sqlite3

import pytest

from pancake_prediction_ai.execution_state import ExecutionIntent, IntentState
from pancake_prediction_ai.execution_store import IntentStore


def intent(intent_id: str, nonce: int) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id=intent_id,
        round_id=100,
        side="BULL",
        amount_wei=10**15,
    ).transition(IntentState.RESERVED, nonce=nonce)


def test_restart_recovers_unresolved_intent(tmp_path) -> None:
    db = tmp_path / "execution.sqlite3"
    with IntentStore(db) as store:
        current = intent("a", 7).transition(IntentState.SUBMITTED, tx_hash="0xabc")
        store.save(current)
        assert store.count_unresolved() == 1

    with IntentStore(db) as reopened:
        recovered = reopened.get("a")
        assert recovered == current
        assert reopened.unresolved() == [current]


def test_nonce_is_unique_across_unresolved_intents(tmp_path) -> None:
    with IntentStore(tmp_path / "execution.sqlite3") as store:
        store.save(intent("a", 7))
        with pytest.raises(sqlite3.IntegrityError):
            store.save(intent("b", 7))


def test_terminal_intent_releases_nonce_reservation(tmp_path) -> None:
    with IntentStore(tmp_path / "execution.sqlite3") as store:
        first = intent("a", 7).transition(IntentState.CANCELLED)
        store.save(first)
        store.save(intent("b", 7))
        assert store.get("b") is not None


def test_state_updates_are_persisted_atomically(tmp_path) -> None:
    with IntentStore(tmp_path / "execution.sqlite3") as store:
        current = intent("a", 9)
        store.save(current)
        submitted = current.transition(IntentState.SUBMITTED, tx_hash="0xabc")
        store.save(submitted)
        assert store.get("a") == submitted
