from pathlib import Path

from pancake_prediction.execution_intent import ExecutionIntentStore, IntentState
from pancake_prediction.execution_report import build_execution_intent_report

SENDER = "0x" + "11" * 20
TARGET = "0x" + "22" * 20


def _store(tmp_path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return store


def _intent(store: ExecutionIntentStore, key: str) -> int:
    return store.get_or_create(
        idempotency_key=key,
        sender=SENDER,
        target=TARGET,
        calldata="0x1234",
        value_wei=1,
    ).id


def test_empty_execution_report_is_gate_ready(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = build_execution_intent_report(store.path)
    assert report.total == 0
    assert report.resolved == 0
    assert report.unresolved == 0
    assert report.unresolved_ids == ()
    assert report.gate_ready is True
    assert report.as_dict()["state_counts"] == {}


def test_finalized_and_failed_are_resolved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    finalized = _intent(store, "finalized")
    failed = _intent(store, "failed")
    store.set_reconciliation_state(finalized, IntentState.FINALIZED)
    store.set_reconciliation_state(failed, IntentState.FAILED, error="reverted")

    report = build_execution_intent_report(store.path)
    assert report.total == 2
    assert report.resolved == 2
    assert report.unresolved == 0
    assert report.gate_ready is True
    assert dict(report.state_counts) == {"failed": 1, "finalized": 1}


def test_consumed_unknown_remains_unresolved_for_stage5_gate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent_id = _intent(store, "ambiguous")
    store.set_reconciliation_state(
        intent_id,
        IntentState.CONSUMED_UNKNOWN,
        error="nonce consumed but transaction unidentified",
    )

    report = build_execution_intent_report(store.path)
    assert report.resolved == 0
    assert report.unresolved == 1
    assert report.unresolved_ids == (intent_id,)
    assert report.gate_ready is False
    assert dict(report.state_counts) == {"consumed_unknown": 1}


def test_all_nonresolved_states_are_reported_in_id_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = _intent(store, "created")
    retryable = _intent(store, "retryable")
    reorged = _intent(store, "reorged")
    store.set_reconciliation_state(retryable, IntentState.RETRYABLE)
    store.set_reconciliation_state(reorged, IntentState.REORGED)

    report = build_execution_intent_report(store.path)
    assert report.unresolved == 3
    assert report.unresolved_ids == (created, retryable, reorged)
    assert report.gate_ready is False
