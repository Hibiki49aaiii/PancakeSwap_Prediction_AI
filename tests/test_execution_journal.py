from pathlib import Path

import pytest

from pancake_prediction.execution_intent import ExecutionIntentStore, IntentState

SENDER = "0x" + "11" * 20
TARGET = "0x" + "22" * 20
TX_HASH = "0x" + "aa" * 32


def _store(tmp_path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return store


def test_transition_journal_preserves_reorg_recovery_path(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent = store.get_or_create(
        idempotency_key="reorg-path",
        sender=SENDER,
        target=TARGET,
        calldata="0x1234",
        value_wei=1,
    )
    store.reserve_nonce(intent.id, 7)
    store.begin_submission(intent.id)
    store.mark_submitted(intent.id, TX_HASH)
    store.set_reconciliation_state(intent.id, IntentState.MINED)
    store.set_reconciliation_state(intent.id, IntentState.REORGED, error="reorg")
    store.set_reconciliation_state(intent.id, IntentState.RETRYABLE, error="nonce free")

    transitions = [
        (row["from_state"], row["to_state"])
        for row in store.transitions(intent.id)
    ]
    assert transitions == [
        (None, "created"),
        ("created", "reserved"),
        ("reserved", "submitting"),
        ("submitting", "submitted"),
        ("submitted", "mined"),
        ("mined", "reorged"),
        ("reorged", "retryable"),
    ]


def test_same_state_reconciliation_does_not_forge_transition(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent = store.get_or_create(
        idempotency_key="same-state",
        sender=SENDER,
        target=TARGET,
        calldata="0x1234",
        value_wei=1,
    )
    store.set_reconciliation_state(intent.id, IntentState.RETRYABLE)
    before = len(store.transitions(intent.id))
    store.set_reconciliation_state(intent.id, IntentState.RETRYABLE)
    assert len(store.transitions(intent.id)) == before


def test_observations_are_idempotent_but_immutable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    detail = {"intent_id": 1, "tx_hash": TX_HASH}
    store.record_observation(
        scenario="dropped_or_replaced_recovery",
        observed=True,
        detail=detail,
    )
    store.record_observation(
        scenario="dropped_or_replaced_recovery",
        observed=True,
        detail=detail,
    )
    rows = store.observations()
    assert len(rows) == 1
    assert rows[0]["observed"] == 1

    with pytest.raises(ValueError, match="different evidence"):
        store.record_observation(
            scenario="dropped_or_replaced_recovery",
            observed=False,
            detail=detail,
        )
