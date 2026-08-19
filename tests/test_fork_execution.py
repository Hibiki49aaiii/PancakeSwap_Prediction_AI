from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from eth_abi import encode
from eth_hash.auto import keccak

from pancake_prediction_ai.abi_codec import function_selector
from pancake_prediction_ai.fork_execution import (
    BEAR_TEST_ACCOUNT,
    BULL_TEST_ACCOUNT,
    Stage5BExecutionBlocked,
    run_stage5b_prediction_execution_probe,
)
from pancake_prediction_ai.fork_harness import ForkProbeResult
from pancake_prediction_ai.pancake_contract import BNB_PREDICTION_CONTRACT


BLOCK_HASH = "0x" + "ab" * 32
PREDICTION = BNB_PREDICTION_CONTRACT
CHAINLINK = "0x2222222222222222222222222222222222222222"
EPOCH = 77
MIN_BET = 100
BASE_BLOCK = 0x1234
BASE_TIMESTAMP = 1005


def _hex_data(types: tuple[str, ...], values: tuple[object, ...]) -> str:
    return "0x" + encode(list(types), list(values)).hex()


def _topic(signature: str) -> str:
    return "0x" + keccak(signature.encode("ascii")).hex()


def verified_fork() -> ForkProbeResult:
    return ForkProbeResult(
        prediction_contract=PREDICTION,
        chainlink_contract=CHAINLINK,
        chain_id=56,
        initial_block=BASE_BLOCK,
        mined_block=BASE_BLOCK + 1,
        reset_block=BASE_BLOCK,
        prediction_contract_code_present=True,
        chainlink_contract_code_present=True,
        prediction_code_present_after_reset=True,
        chainlink_code_present_after_reset=True,
        fork_reset_supported=True,
        fork_mine_observed=True,
        upstream_chain_id=56,
        local_initial_block_hash=BLOCK_HASH,
        upstream_fork_block_hash=BLOCK_HASH,
        local_reset_block_hash=BLOCK_HASH,
        fork_block_hash_matches_upstream=True,
        reset_block_hash_matches_upstream=True,
        prediction_code_matches_upstream=True,
        chainlink_code_matches_upstream=True,
        prediction_code_matches_upstream_after_reset=True,
        chainlink_code_matches_upstream_after_reset=True,
        upstream_verified=True,
    )


class FakePredictionForkRpc:
    def __init__(
        self,
        *,
        timestamp: int = BASE_TIMESTAMP,
        omit_bull_event: bool = False,
        reset_restores: bool = True,
    ) -> None:
        self.block = BASE_BLOCK
        self.timestamp = timestamp
        self.base_timestamp = timestamp
        self.omit_bull_event = omit_bull_event
        self.reset_restores = reset_restores
        self.base_round = {
            "epoch": EPOCH,
            "start": 1000,
            "lock": 1100,
            "close": 1200,
            "lock_price": 0,
            "close_price": 0,
            "lock_oracle_id": 0,
            "close_oracle_id": 0,
            "total": 1000,
            "bull": 600,
            "bear": 400,
            "reward_base": 0,
            "reward": 0,
            "oracle_called": False,
        }
        self.round = deepcopy(self.base_round)
        self.ledger: dict[str, tuple[int, int, bool]] = {}
        self.receipts: dict[str, dict[str, object]] = {}
        self.tx_counter = 0
        self.impersonated: set[str] = set()
        self.balances: dict[str, int] = {}

    @staticmethod
    def _selector(data: str) -> bytes:
        return bytes.fromhex(data[2:10])

    @staticmethod
    def _word(data: str, index: int) -> bytes:
        start = 10 + index * 64
        return bytes.fromhex(data[start : start + 64])

    def _decode_epoch(self, data: str) -> int:
        return int.from_bytes(self._word(data, 0), "big")

    def _decode_account(self, data: str) -> str:
        return "0x" + self._word(data, 1)[12:].hex()

    def _round_result(self) -> str:
        r = self.round
        return _hex_data(
            (
                "uint256",
                "uint256",
                "uint256",
                "uint256",
                "int256",
                "int256",
                "uint256",
                "uint256",
                "uint256",
                "uint256",
                "uint256",
                "uint256",
                "uint256",
                "bool",
            ),
            (
                r["epoch"],
                r["start"],
                r["lock"],
                r["close"],
                r["lock_price"],
                r["close_price"],
                r["lock_oracle_id"],
                r["close_oracle_id"],
                r["total"],
                r["bull"],
                r["bear"],
                r["reward_base"],
                r["reward"],
                r["oracle_called"],
            ),
        )

    def _bettable(self) -> bool:
        return (
            self.round["start"] != 0
            and self.round["lock"] != 0
            and self.timestamp > self.round["start"]
            and self.timestamp < self.round["lock"]
        )

    def _validate_bet(self, *, account: str, epoch: int, value: int) -> None:
        if epoch != EPOCH:
            raise RuntimeError("Bet is too early/late")
        if not self._bettable():
            raise RuntimeError("Round not bettable")
        if value < MIN_BET:
            raise RuntimeError("Bet amount must be greater than minBetAmount")
        if self.ledger.get(account, (0, 0, False))[1] != 0:
            raise RuntimeError("Can only bet once per round")

    def _bet_event(self, *, side: str, account: str, epoch: int, amount: int) -> dict[str, object]:
        signature = "BetBull(address,uint256,uint256)" if side == "BULL" else "BetBear(address,uint256,uint256)"
        return {
            "address": PREDICTION,
            "topics": [
                _topic(signature),
                "0x" + account[2:].rjust(64, "0"),
                "0x" + epoch.to_bytes(32, "big").hex(),
            ],
            "data": "0x" + amount.to_bytes(32, "big").hex(),
        }

    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_getBlockByNumber":
            requested = params[0]
            number = self.block if requested == "latest" else int(str(requested), 16)
            return {
                "number": hex(number),
                "hash": BLOCK_HASH if number == BASE_BLOCK else "0x" + "cd" * 32,
                "timestamp": hex(self.timestamp),
            }
        if method == "eth_call":
            tx = params[0]
            assert isinstance(tx, dict)
            data = str(tx["data"])
            selector = self._selector(data)
            if selector == function_selector("currentEpoch()"):
                return _hex_data(("uint256",), (EPOCH,))
            if selector == function_selector("minBetAmount()"):
                return _hex_data(("uint256",), (MIN_BET,))
            if selector == function_selector("rounds(uint256)"):
                assert self._decode_epoch(data) == EPOCH
                return self._round_result()
            if selector == function_selector("ledger(uint256,address)"):
                assert self._decode_epoch(data) == EPOCH
                account = self._decode_account(data)
                position, amount, claimed = self.ledger.get(account, (0, 0, False))
                return _hex_data(("uint8", "uint256", "bool"), (position, amount, claimed))
            if selector in {
                function_selector("betBull(uint256)"),
                function_selector("betBear(uint256)"),
            }:
                account = str(tx.get("from", "")).lower()
                value = int(str(tx.get("value", "0x0")), 16)
                self._validate_bet(account=account, epoch=self._decode_epoch(data), value=value)
                return "0x"
            raise KeyError(f"unknown eth_call selector {selector.hex()}")
        if method == "anvil_setBalance":
            self.balances[str(params[0]).lower()] = int(str(params[1]), 16)
            return True
        if method == "anvil_impersonateAccount":
            self.impersonated.add(str(params[0]).lower())
            return True
        if method == "anvil_stopImpersonatingAccount":
            self.impersonated.discard(str(params[0]).lower())
            return True
        if method == "eth_sendTransaction":
            tx = params[0]
            assert isinstance(tx, dict)
            account = str(tx["from"]).lower()
            assert account in self.impersonated
            data = str(tx["data"])
            epoch = self._decode_epoch(data)
            value = int(str(tx["value"]), 16)
            self._validate_bet(account=account, epoch=epoch, value=value)
            selector = self._selector(data)
            side = "BULL" if selector == function_selector("betBull(uint256)") else "BEAR"
            position = 0 if side == "BULL" else 1
            self.ledger[account] = (position, value, False)
            self.round["total"] += value
            self.round["bull" if side == "BULL" else "bear"] += value
            self.block += 1
            self.tx_counter += 1
            tx_hash = "0x" + self.tx_counter.to_bytes(32, "big").hex()
            logs: list[dict[str, object]] = []
            if not (self.omit_bull_event and side == "BULL"):
                logs.append(self._bet_event(side=side, account=account, epoch=epoch, amount=value))
            self.receipts[tx_hash] = {
                "status": "0x1",
                "blockNumber": hex(self.block),
                "logs": logs,
            }
            return tx_hash
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(str(params[0]))
        if method == "evm_mine":
            self.block += 1
            return True
        if method == "anvil_reset":
            self.block = BASE_BLOCK
            self.timestamp = self.base_timestamp
            if self.reset_restores:
                self.round = deepcopy(self.base_round)
                self.ledger.clear()
                self.receipts.clear()
            return True
        raise KeyError(method)


def test_execution_probe_exercises_bull_bear_revert_and_reset_paths() -> None:
    result = run_stage5b_prediction_execution_probe(
        FakePredictionForkRpc(),
        fork_result=verified_fork(),
        stake_wei=MIN_BET,
    )
    assert result.passed
    assert result.bettable_window_observed
    assert result.bull_tx_mined_success
    assert result.bull_event_observed
    assert result.bull_ledger_matches
    assert result.bull_pool_delta_matches
    assert result.duplicate_bull_reverted
    assert result.below_minimum_bear_reverted
    assert result.state_restored_after_bull_reset
    assert result.bear_tx_mined_success
    assert result.bear_event_observed
    assert result.bear_ledger_matches
    assert result.bear_pool_delta_matches
    assert result.state_restored_after_bear_reset
    assert result.bull_test_account == BULL_TEST_ACCOUNT
    assert result.bear_test_account == BEAR_TEST_ACCOUNT
    assert not result.private_key_used
    assert not result.raw_signed_transaction_used
    assert not result.mainnet_transaction_broadcast


def test_execution_probe_rejects_unverified_fork() -> None:
    bad = replace(verified_fork(), upstream_verified=False)
    with pytest.raises(ValueError, match="verified fork"):
        run_stage5b_prediction_execution_probe(FakePredictionForkRpc(), fork_result=bad)


def test_execution_probe_blocks_when_current_round_is_not_safely_bettable() -> None:
    with pytest.raises(Stage5BExecutionBlocked, match="betting window"):
        run_stage5b_prediction_execution_probe(
            FakePredictionForkRpc(timestamp=1099),
            fork_result=verified_fork(),
            min_window_margin_seconds=3,
        )


def test_execution_probe_rejects_stake_below_current_minimum() -> None:
    with pytest.raises(ValueError, match="minBetAmount"):
        run_stage5b_prediction_execution_probe(
            FakePredictionForkRpc(),
            fork_result=verified_fork(),
            stake_wei=MIN_BET - 1,
        )


def test_execution_probe_fails_evidence_quality_when_expected_event_is_missing() -> None:
    result = run_stage5b_prediction_execution_probe(
        FakePredictionForkRpc(omit_bull_event=True),
        fork_result=verified_fork(),
        stake_wei=MIN_BET,
    )
    assert not result.passed
    assert not result.bull_event_observed


def test_execution_probe_detects_reset_that_does_not_restore_contract_state() -> None:
    result = run_stage5b_prediction_execution_probe(
        FakePredictionForkRpc(reset_restores=False),
        fork_result=verified_fork(),
        stake_wei=MIN_BET,
    )
    assert not result.passed
    assert not result.state_restored_after_bull_reset
