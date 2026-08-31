import json
from pathlib import Path

import pytest

from pancake_prediction import cli
from pancake_prediction.execution_intent import ExecutionIntentStore, IntentState

SENDER = "0x" + "11" * 20
TARGET = "0x" + "22" * 20


def _store(path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(path)
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


def test_cli_fork_intent_report_returns_zero_when_gate_ready(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = _store(database)
    intent_id = _intent(store, "done")
    store.set_reconciliation_state(intent_id, IntentState.FINALIZED)

    assert cli.main(["fork-intent-report", "--db", str(database)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gate_ready"] is True
    assert payload["unresolved"] == 0
    assert payload["state_counts"] == {"finalized": 1}


def test_cli_fork_intent_report_returns_two_for_consumed_unknown(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "execution.sqlite3"
    store = _store(database)
    intent_id = _intent(store, "ambiguous")
    store.set_reconciliation_state(intent_id, IntentState.CONSUMED_UNKNOWN)

    assert cli.main(["fork-intent-report", "--db", str(database)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["gate_ready"] is False
    assert payload["unresolved"] == 1
    assert payload["unresolved_ids"] == [intent_id]
    assert payload["state_counts"] == {"consumed_unknown": 1}
