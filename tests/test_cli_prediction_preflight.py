import json
from pathlib import Path
from typing import Any

import pytest

from pancake_prediction import cli
from pancake_prediction.execution_intent import ExecutionIntentStore
from pancake_prediction.prediction_preflight import (
    CURRENT_EPOCH_SELECTOR,
    LEDGER_SELECTOR,
    MIN_BET_AMOUNT_SELECTOR,
    PAUSED_SELECTOR,
    ROUNDS_SELECTOR,
)
from pancake_prediction.prediction_tx import BetSide, build_prediction_bet_intent

SENDER = "0x" + "11" * 20
TX_HASH = "0x" + "aa" * 32


def _words(*values: int) -> str:
    return "0x" + "".join(f"{value:064x}" for value in values)


class _CliForkRpc:
    def __init__(self) -> None:
        self.paused = False
        self.pending_nonce = 7
        self.send_calls = 0
        self.transactions: dict[str, dict[str, Any]] = {}

    @property
    def fork_only(self) -> bool:
        return True

    def block_number(self) -> int:
        return 500

    def block(self, number: int) -> dict[str, Any]:
        assert number == 500
        return {"hash": "0x" + "bb" * 32, "timestamp": hex(1_500)}

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to
        assert block == 500
        if data == CURRENT_EPOCH_SELECTOR:
            return _words(123)
        if data == MIN_BET_AMOUNT_SELECTOR:
            return _words(100)
        if data == PAUSED_SELECTOR:
            return _words(int(self.paused))
        if data.startswith(ROUNDS_SELECTOR):
            return _words(123, 1_200, 1_600, 1_900, 0, 0, 0, 0, 1_000, 500, 500, 0, 0, 0)
        if data.startswith(LEDGER_SELECTOR):
            return _words(0, 0, 0)
        raise AssertionError(f"unexpected eth_call: {data}")

    def get_code(self, address: str, block: int | str = "latest") -> str:
        assert address == SENDER
        assert block == 500
        return "0x"

    def balance(self, address: str, tag: int | str = "latest") -> int:
        assert address == SENDER
        assert tag == 500
        return 10_000

    def transaction_count(self, address: str, tag: str = "pending") -> int:
        assert address == SENDER
        assert tag == "pending"
        return self.pending_nonce

    def transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        del tx_hash
        return None

    def transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        return self.transactions.get(tx_hash)

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
    ) -> str:
        del to, data, value_wei, gas, gas_price_wei
        assert from_address == SENDER
        assert nonce == 7
        self.send_calls += 1
        self.pending_nonce = 8
        self.transactions[TX_HASH] = {"hash": TX_HASH, "nonce": "0x7"}
        return TX_HASH


def _create_intent(database: Path) -> int:
    store = ExecutionIntentStore(database)
    store.initialize()
    return build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=SENDER,
        epoch=123,
        side=BetSide.BULL,
        stake_wei=1_000,
    ).id


def test_cli_fork_bet_preflight_reports_ready_without_printing_rpc_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "fork.sqlite3"
    intent_id = _create_intent(database)
    rpc = _CliForkRpc()
    monkeypatch.setattr(cli, "LocalForkRpcClient", lambda url: rpc)

    secretish_url = "http://127.0.0.1:8545/path-token"
    exit_code = cli.main(
        [
            "fork-bet-preflight",
            "--fork-rpc-url",
            secretish_url,
            "--db",
            str(database),
            "--intent-id",
            str(intent_id),
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert payload["ready"] is True
    assert payload["snapshot_block"] == 500
    assert secretish_url not in output


def test_cli_fork_bet_preflight_returns_nonzero_when_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "fork.sqlite3"
    intent_id = _create_intent(database)
    rpc = _CliForkRpc()
    rpc.paused = True
    monkeypatch.setattr(cli, "LocalForkRpcClient", lambda url: rpc)

    exit_code = cli.main(
        [
            "fork-bet-preflight",
            "--fork-rpc-url",
            "http://127.0.0.1:8545",
            "--db",
            str(database),
            "--intent-id",
            str(intent_id),
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["ready"] is False
    assert "prediction contract is paused" in payload["reasons"]


def test_cli_submit_fails_before_nonce_or_send_when_preflight_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "fork.sqlite3"
    intent_id = _create_intent(database)
    rpc = _CliForkRpc()
    rpc.paused = True
    monkeypatch.setattr(cli, "LocalForkRpcClient", lambda url: rpc)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "fork-submit-intent",
                "--fork-rpc-url",
                "http://127.0.0.1:8545",
                "--db",
                str(database),
                "--intent-id",
                str(intent_id),
            ]
        )
    assert exc_info.value.code == 2
    assert rpc.send_calls == 0
    assert rpc.pending_nonce == 7


def test_cli_submit_sends_once_after_ready_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "fork.sqlite3"
    intent_id = _create_intent(database)
    rpc = _CliForkRpc()
    monkeypatch.setattr(cli, "LocalForkRpcClient", lambda url: rpc)

    assert (
        cli.main(
            [
                "fork-submit-intent",
                "--fork-rpc-url",
                "http://127.0.0.1:8545",
                "--db",
                str(database),
                "--intent-id",
                str(intent_id),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "submitted"
    assert payload["nonce"] == 7
    assert payload["current_tx_hash"] == TX_HASH
    assert rpc.send_calls == 1
