from __future__ import annotations

import unittest

from pancake_prediction.abi import BET_EVENT_TOPICS, encode_bet_calldata
from pancake_prediction.contracts import CHAIN_ID_BSC, MARKETS, market
from pancake_prediction.execution import build_unsigned_bet, normalize_evm_address
from pancake_prediction.rpc import LocalForkRpcClient


WALLET = "0x1111111111111111111111111111111111111111"


class ContractRegistryTests(unittest.TestCase):
    def test_current_market_registry(self) -> None:
        self.assertEqual(CHAIN_ID_BSC, 56)
        self.assertEqual(set(MARKETS), {"BNBUSD", "BTCUSD", "ETHUSD"})
        self.assertEqual(
            MARKETS["BNBUSD"].address,
            "0x18b2a687610328590bc8f2e5fedde3b582a49cda",
        )

    def test_market_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(market("ethusd").symbol, "ETHUSD")

    def test_unknown_market_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            market("DOGEUSD")


class AbiTests(unittest.TestCase):
    def test_bull_selector_and_epoch_encoding(self) -> None:
        encoded = encode_bet_calldata("bull", 42)
        self.assertTrue(encoded.startswith("0x57fb096f"))
        self.assertEqual(len(encoded), 10 + 64)
        self.assertEqual(int(encoded[-64:], 16), 42)

    def test_bear_selector_and_event_topic_are_pinned(self) -> None:
        self.assertTrue(encode_bet_calldata("bear", 9).startswith("0xaa6b873a"))
        self.assertEqual(
            BET_EVENT_TOPICS["bear"],
            "0x0d8c1fe3e67ab767116a81f122b83c2557a8c2564019cb7c4f83de1aeb1f1f0d",
        )

    def test_invalid_epoch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_bet_calldata("bull", -1)


class ExecutionBoundaryTests(unittest.TestCase):
    def test_unsigned_payload_is_deterministic(self) -> None:
        first = build_unsigned_bet(
            wallet_address=WALLET,
            market_symbol="BNBUSD",
            epoch=123,
            side="bull",
            value_wei=10**15,
        )
        second = build_unsigned_bet(
            wallet_address=WALLET,
            market_symbol="bnbusd",
            epoch=123,
            side="BULL",
            value_wei=10**15,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.target_address, MARKETS["BNBUSD"].address)
        self.assertEqual(len(first.semantic_payload_hash), 66)

    def test_semantic_hash_changes_when_stake_changes(self) -> None:
        first = build_unsigned_bet(
            wallet_address=WALLET,
            market_symbol="BNBUSD",
            epoch=123,
            side="bull",
            value_wei=10**15,
        )
        second = build_unsigned_bet(
            wallet_address=WALLET,
            market_symbol="BNBUSD",
            epoch=123,
            side="bull",
            value_wei=2 * 10**15,
        )
        self.assertNotEqual(first.semantic_payload_hash, second.semantic_payload_hash)

    def test_address_validation(self) -> None:
        self.assertEqual(normalize_evm_address(WALLET.upper().replace("0X", "0x")), WALLET)
        with self.assertRaises(ValueError):
            normalize_evm_address("0x1234")


class ForkSafetyTests(unittest.TestCase):
    def test_local_fork_accepts_only_loopback(self) -> None:
        LocalForkRpcClient("http://127.0.0.1:8545")
        LocalForkRpcClient("http://localhost:8545")
        LocalForkRpcClient("http://[::1]:8545")
        for url in (
            "https://bsc-dataseed.binance.org",
            "http://192.168.1.20:8545",
            "http://example.com:8545",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalForkRpcClient(url)


if __name__ == "__main__":
    unittest.main()
