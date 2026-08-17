from pathlib import Path

import pytest

from pancake_prediction.abi import function_selector
from pancake_prediction.contracts import MARKETS
from pancake_prediction.execution_intent import ExecutionIntentStore
from pancake_prediction.prediction_tx import (
    BetSide,
    build_prediction_bet_intent,
    encode_bet_calldata,
    encode_uint256,
    prediction_bet_idempotency_key,
)

SENDER = "0x" + "11" * 20


def _store(tmp_path: Path) -> ExecutionIntentStore:
    store = ExecutionIntentStore(tmp_path / "execution.sqlite3")
    store.initialize()
    return store


def test_bet_calldata_uses_expected_selector_and_uint256_epoch() -> None:
    epoch = 123456
    bull = encode_bet_calldata(BetSide.BULL, epoch)
    bear = encode_bet_calldata(BetSide.BEAR, epoch)

    assert bull == function_selector("betBull(uint256)") + f"{epoch:064x}"
    assert bear == function_selector("betBear(uint256)") + f"{epoch:064x}"
    assert bull != bear
    assert len(bull) == 74


def test_uint256_encoder_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="out of range"):
        encode_uint256(-1)
    with pytest.raises(ValueError, match="out of range"):
        encode_uint256(1 << 256)


def test_prediction_bet_intent_targets_market_contract_and_stake(tmp_path: Path) -> None:
    store = _store(tmp_path)
    intent = build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=SENDER,
        epoch=123456,
        side=BetSide.BULL,
        stake_wei=10**15,
    )

    assert intent.target == MARKETS["BNBUSD"].address.lower()
    assert intent.value_wei == 10**15
    assert intent.calldata == encode_bet_calldata(BetSide.BULL, 123456)
    assert intent.idempotency_key == prediction_bet_idempotency_key("BNBUSD", SENDER, 123456)


def test_same_wallet_round_cannot_create_opposite_side_intent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=SENDER,
        epoch=123456,
        side=BetSide.BULL,
        stake_wei=10**15,
    )
    same = build_prediction_bet_intent(
        store,
        market="BNBUSD",
        sender=SENDER,
        epoch=123456,
        side=BetSide.BULL,
        stake_wei=10**15,
    )
    assert same.id == first.id

    with pytest.raises(ValueError, match="different payload"):
        build_prediction_bet_intent(
            store,
            market="BNBUSD",
            sender=SENDER,
            epoch=123456,
            side=BetSide.BEAR,
            stake_wei=10**15,
        )


def test_intent_builder_rejects_invalid_market_epoch_and_stake(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        build_prediction_bet_intent(
            store,
            market="BNBUSD",
            sender=SENDER,
            epoch=1,
            side=BetSide.BULL,
            stake_wei=0,
        )
    with pytest.raises(ValueError, match="unsupported"):
        build_prediction_bet_intent(
            store,
            market="DOGEUSD",
            sender=SENDER,
            epoch=1,
            side=BetSide.BULL,
            stake_wei=1,
        )
    with pytest.raises(ValueError, match="non-negative"):
        prediction_bet_idempotency_key("BNBUSD", SENDER, -1)
