from __future__ import annotations

from enum import StrEnum

from .abi import function_selector
from .contracts import MARKETS
from .execution_intent import ExecutionIntent, ExecutionIntentStore


class BetSide(StrEnum):
    BULL = "bull"
    BEAR = "bear"


BET_SIGNATURES = {
    BetSide.BULL: "betBull(uint256)",
    BetSide.BEAR: "betBear(uint256)",
}


def encode_uint256(value: int) -> str:
    if value < 0 or value >= 1 << 256:
        raise ValueError("uint256 value is out of range")
    return f"{value:064x}"


def encode_bet_calldata(side: BetSide, epoch: int) -> str:
    selector = function_selector(BET_SIGNATURES[side])
    return selector + encode_uint256(epoch)


def prediction_bet_idempotency_key(market: str, sender: str, epoch: int) -> str:
    if market not in MARKETS:
        raise ValueError(f"unsupported prediction market: {market}")
    if epoch < 0:
        raise ValueError("epoch must be non-negative")
    return f"prediction-bet:{market}:{sender.lower()}:{epoch}"


def build_prediction_bet_intent(
    store: ExecutionIntentStore,
    *,
    market: str,
    sender: str,
    epoch: int,
    side: BetSide,
    stake_wei: int,
) -> ExecutionIntent:
    if stake_wei <= 0:
        raise ValueError("stake_wei must be positive")
    if market not in MARKETS:
        raise ValueError(f"unsupported prediction market: {market}")
    market_config = MARKETS[market]
    return store.get_or_create(
        idempotency_key=prediction_bet_idempotency_key(market, sender, epoch),
        sender=sender,
        target=market_config.address,
        calldata=encode_bet_calldata(side, epoch),
        value_wei=stake_wei,
    )
