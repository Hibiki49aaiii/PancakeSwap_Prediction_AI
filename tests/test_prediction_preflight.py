from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.execution_intent import ExecutionIntentStore
from pancake_prediction.prediction_preflight import (
    CURRENT_EPOCH_SELECTOR,
    LEDGER_SELECTOR,
    MIN_BET_AMOUNT_SELECTOR,
    PAUSED_SELECTOR,
    ROUNDS_SELECTOR,
    inspect_prediction_bet_intent,
    require_prediction_bet_ready,
)
from pancake_prediction.prediction_tx import BetSide, build_prediction_bet_intent

SENDER = "0x" + "11" * 20


def _words(*values: int) -> str:
    return "0x" + "".join(f"{value:064x}" for value in values)


class _FakePreflightRpc:
    def __init__(self) -> None:
        self.head = 500
        self.timestamp = 1_500
        self.current_epoch = 123
        self.min_bet = 100
        self.paused = False
        self.round_epoch = 123
        self.start = 1_200
        self.lock = 1_600
        self.existing_bet = 0
        self.sender_code = "0x"
        self.sender_balance = 10_000
        self.call_blocks: list[int | str] = []
        self.code_blocks: list[int | str] = []
        self.balance_tags: list[int | str] = []

    def block_number(self) -> int:
        return self.head

    def block(self, number: int) -> dict[str, Any]:
        assert number == self.head
        return {"number": hex(number), "timestamp": hex(self.timestamp), "hash": "0x" + "aa" * 32}

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to
        self.call_blocks.append(block)
        if data == CURRENT_EPOCH_SELECTOR:
            return _words(self.current_epoch)
        if data == MIN_BET_AMOUNT_SELECTOR:
            return _words(self.min_bet)
        if data == PAUSED_SELECTOR:
            return _words(int(self.paused))
        if data.startswith(ROUNDS_SELECTOR):
            return _words(
                self.round_epoch,
                self.start,
                self.lock,
                self.lock + 300,
                0,
                0,
                0,
                0,
                1_000,
                500,
                500,
                0,
                0,
                0,
            )
        if data.startswith(LEDGER_SELECTOR):
            return _words(0, self.existing_bet, 0)
        raise AssertionError(f"unexpected eth_call data: {data}")

    def get_code(self, address: str, block: int | str = "latest") -> str:
        assert address == SENDER
        self.code_blocks.append(block)
        return self.sender_code

    def balance(self, address: str, tag: int | str = "latest") -> int:
        assert address == SENDER
        self.balance_tags.append(tag)
        return self.sender_balance


def _intent(tmp_path: Path, *, epoch: int = 123, stake: int = 1_000) -> object:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=SENDER,
        epoch=epoch,
        side=BetSide.BULL,
        stake_wei=stake,
    )


def test_ready_preflight_uses_one_fixed_block_snapshot(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    intent = _intent(tmp_path)
    result = inspect_prediction_bet_intent(rpc, intent)  # type: ignore[arg-type]

    assert result.ready is True
    assert result.reasons == ()
    assert result.market == "BNBUSD"
    assert result.epoch == 123
    assert result.side == BetSide.BULL
    assert result.snapshot_block == 500
    assert result.snapshot_timestamp == 1_500
    assert result.current_epoch == 123
    assert result.start_timestamp == 1_200
    assert result.lock_timestamp == 1_600
    assert result.min_bet_amount_wei == 100
    assert result.existing_bet_amount_wei == 0
    assert result.sender_code_present is False
    assert rpc.call_blocks and set(rpc.call_blocks) == {500}
    assert rpc.code_blocks == [500]
    assert rpc.balance_tags == [500]


def test_preflight_rejects_paused_contract(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.paused = True
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    assert result.ready is False
    assert "prediction contract is paused" in result.reasons


def test_preflight_rejects_non_current_epoch(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.current_epoch = 124
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    assert result.ready is False
    assert any("not current epoch" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    ("timestamp", "message"),
    [(1_200, "has not opened"), (1_600, "is closed"), (1_601, "is closed")],
)
def test_preflight_enforces_strict_betting_window(
    tmp_path: Path,
    timestamp: int,
    message: str,
) -> None:
    rpc = _FakePreflightRpc()
    rpc.timestamp = timestamp
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    assert result.ready is False
    assert any(message in reason for reason in result.reasons)


def test_preflight_rejects_below_minimum_stake(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path, stake=99))  # type: ignore[arg-type]
    assert result.ready is False
    assert any("below minBetAmount" in reason for reason in result.reasons)


def test_preflight_rejects_existing_bet(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.existing_bet = 200
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    assert result.ready is False
    assert "sender already has a bet in this round" in result.reasons


def test_preflight_rejects_contract_sender(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.sender_code = "0x6001"
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    assert result.ready is False
    assert "sender has contract code and would fail notContract" in result.reasons


def test_preflight_rejects_insufficient_stake_balance(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.sender_balance = 999
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path, stake=1_000))  # type: ignore[arg-type]
    assert result.ready is False
    assert "sender balance is below the intended stake" in result.reasons


def test_preflight_requires_round_getter_epoch_match(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.round_epoch = 0
    result = inspect_prediction_bet_intent(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    assert result.ready is False
    assert any("round getter epoch" in reason for reason in result.reasons)


def test_require_ready_raises_with_all_reasons(tmp_path: Path) -> None:
    rpc = _FakePreflightRpc()
    rpc.paused = True
    rpc.current_epoch = 124
    rpc.existing_bet = 1
    with pytest.raises(ValueError, match="Prediction bet preflight failed") as exc_info:
        require_prediction_bet_ready(rpc, _intent(tmp_path))  # type: ignore[arg-type]
    message = str(exc_info.value)
    assert "paused" in message
    assert "not current epoch" in message
    assert "already has a bet" in message
