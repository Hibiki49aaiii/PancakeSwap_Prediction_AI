from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .abi import function_selector
from .contracts import MARKETS
from .execution_intent import ExecutionIntent
from .prediction_tx import BET_SIGNATURES, BetSide

CURRENT_EPOCH_SELECTOR = function_selector("currentEpoch()")
MIN_BET_AMOUNT_SELECTOR = function_selector("minBetAmount()")
PAUSED_SELECTOR = function_selector("paused()")
ROUNDS_SELECTOR = function_selector("rounds(uint256)")
LEDGER_SELECTOR = function_selector("ledger(uint256,address)")
BET_SELECTORS = {function_selector(signature): side for side, signature in BET_SIGNATURES.items()}


class PredictionPreflightRpc(Protocol):
    def block_number(self) -> int: ...

    def block(self, number: int) -> dict[str, Any]: ...

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str: ...

    def get_code(self, address: str, block: int | str = "latest") -> str: ...

    def balance(self, address: str, tag: int | str = "latest") -> int: ...


@dataclass(frozen=True, slots=True)
class PredictionBetPreflight:
    market: str
    sender: str
    epoch: int
    side: BetSide
    stake_wei: int
    snapshot_block: int
    snapshot_timestamp: int
    current_epoch: int
    min_bet_amount_wei: int
    paused: bool
    round_epoch: int
    start_timestamp: int
    lock_timestamp: int
    existing_bet_amount_wei: int
    sender_balance_wei: int
    sender_code_present: bool
    ready: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "sender": self.sender,
            "epoch": self.epoch,
            "side": self.side.value,
            "stake_wei": self.stake_wei,
            "snapshot_block": self.snapshot_block,
            "snapshot_timestamp": self.snapshot_timestamp,
            "current_epoch": self.current_epoch,
            "min_bet_amount_wei": self.min_bet_amount_wei,
            "paused": self.paused,
            "round_epoch": self.round_epoch,
            "start_timestamp": self.start_timestamp,
            "lock_timestamp": self.lock_timestamp,
            "existing_bet_amount_wei": self.existing_bet_amount_wei,
            "sender_balance_wei": self.sender_balance_wei,
            "sender_code_present": self.sender_code_present,
            "ready": self.ready,
            "reasons": list(self.reasons),
        }


def _encode_uint256_word(value: int) -> str:
    if value < 0 or value >= 1 << 256:
        raise ValueError("uint256 value is out of range")
    return f"{value:064x}"


def _encode_address_word(address: str) -> str:
    text = address.lower().removeprefix("0x")
    if len(text) != 40:
        raise ValueError("address must be a 20-byte hex address")
    try:
        int(text, 16)
    except ValueError as exc:
        raise ValueError("address must be hexadecimal") from exc
    return "0" * 24 + text


def _decode_words(result: str, expected_words: int) -> tuple[int, ...]:
    raw_hex = result.removeprefix("0x")
    if len(raw_hex) != expected_words * 64:
        actual_words = len(raw_hex) // 64
        raise ValueError(
            f"unexpected ABI result length: expected {expected_words} words, "
            f"got {actual_words}"
        )
    try:
        return tuple(
            int(raw_hex[index * 64 : (index + 1) * 64], 16)
            for index in range(expected_words)
        )
    except ValueError as exc:
        raise ValueError("ABI result is not hexadecimal") from exc


def _decode_bool_word(value: int, *, name: str) -> bool:
    if value not in {0, 1}:
        raise ValueError(f"{name} ABI bool must be 0 or 1")
    return bool(value)


def _quantity(value: object) -> int:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _parse_bet_intent(intent: ExecutionIntent) -> tuple[str, BetSide, int]:
    market = next(
        (
            symbol
            for symbol, config in MARKETS.items()
            if config.address.lower() == intent.target.lower()
        ),
        None,
    )
    if market is None:
        raise ValueError("intent target is not a registered Prediction market")
    calldata = intent.calldata.lower()
    if not calldata.startswith("0x") or len(calldata) != 74:
        raise ValueError("intent calldata is not a single uint256 Prediction bet call")
    selector = calldata[:10]
    side = BET_SELECTORS.get(selector)
    if side is None:
        raise ValueError("intent calldata is not betBull(uint256) or betBear(uint256)")
    try:
        epoch = int(calldata[10:], 16)
    except ValueError as exc:
        raise ValueError("intent epoch calldata is not hexadecimal") from exc
    return market, side, epoch


def inspect_prediction_bet_intent(
    rpc: PredictionPreflightRpc,
    intent: ExecutionIntent,
) -> PredictionBetPreflight:
    market, side, epoch = _parse_bet_intent(intent)
    snapshot_block = rpc.block_number()
    block = rpc.block(snapshot_block)
    snapshot_timestamp = _quantity(block["timestamp"])

    current_epoch = _decode_words(
        rpc.eth_call(intent.target, CURRENT_EPOCH_SELECTOR, snapshot_block), 1
    )[0]
    min_bet_amount = _decode_words(
        rpc.eth_call(intent.target, MIN_BET_AMOUNT_SELECTOR, snapshot_block), 1
    )[0]
    paused_result = rpc.eth_call(intent.target, PAUSED_SELECTOR, snapshot_block)
    paused_word = _decode_words(paused_result, 1)[0]
    paused = _decode_bool_word(paused_word, name="paused")

    round_words = _decode_words(
        rpc.eth_call(
            intent.target,
            ROUNDS_SELECTOR + _encode_uint256_word(epoch),
            snapshot_block,
        ),
        14,
    )
    round_epoch = round_words[0]
    start_timestamp = round_words[1]
    lock_timestamp = round_words[2]

    ledger_calldata = (
        LEDGER_SELECTOR
        + _encode_uint256_word(epoch)
        + _encode_address_word(intent.sender)
    )
    ledger_words = _decode_words(
        rpc.eth_call(intent.target, ledger_calldata, snapshot_block),
        3,
    )
    existing_bet_amount = ledger_words[1]
    sender_code = rpc.get_code(intent.sender, snapshot_block).lower()
    sender_code_present = sender_code not in {"", "0x", "0x0"}
    sender_balance = rpc.balance(intent.sender, snapshot_block)

    reasons: list[str] = []
    if paused:
        reasons.append("prediction contract is paused")
    if epoch != current_epoch:
        reasons.append(f"intent epoch {epoch} is not current epoch {current_epoch}")
    if round_epoch != epoch:
        reasons.append(f"round getter epoch {round_epoch} does not match intent epoch {epoch}")
    if start_timestamp == 0 or lock_timestamp == 0:
        reasons.append("round has not been initialized with start/lock timestamps")
    elif snapshot_timestamp <= start_timestamp:
        reasons.append("round betting window has not opened")
    elif snapshot_timestamp >= lock_timestamp:
        reasons.append("round betting window is closed")
    if intent.value_wei < min_bet_amount:
        reasons.append(f"stake {intent.value_wei} is below minBetAmount {min_bet_amount}")
    if existing_bet_amount != 0:
        reasons.append("sender already has a bet in this round")
    if sender_code_present:
        reasons.append("sender has contract code and would fail notContract")
    if sender_balance < intent.value_wei:
        reasons.append("sender balance is below the intended stake")

    return PredictionBetPreflight(
        market=market,
        sender=intent.sender,
        epoch=epoch,
        side=side,
        stake_wei=intent.value_wei,
        snapshot_block=snapshot_block,
        snapshot_timestamp=snapshot_timestamp,
        current_epoch=current_epoch,
        min_bet_amount_wei=min_bet_amount,
        paused=paused,
        round_epoch=round_epoch,
        start_timestamp=start_timestamp,
        lock_timestamp=lock_timestamp,
        existing_bet_amount_wei=existing_bet_amount,
        sender_balance_wei=sender_balance,
        sender_code_present=sender_code_present,
        ready=not reasons,
        reasons=tuple(reasons),
    )


def require_prediction_bet_ready(
    rpc: PredictionPreflightRpc,
    intent: ExecutionIntent,
) -> PredictionBetPreflight:
    result = inspect_prediction_bet_intent(rpc, intent)
    if not result.ready:
        raise ValueError("Prediction bet preflight failed: " + "; ".join(result.reasons))
    return result
