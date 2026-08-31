from __future__ import annotations

import hashlib

import pytest

from pancake_prediction.contracts import MARKETS, Market
from pancake_prediction.deployment_provenance import (
    DeploymentProvenanceError,
    decode_prediction_v2_constructor_input,
    decode_prediction_v2_creation_transaction,
)


def _address_word(address: str) -> str:
    return "0" * 24 + address.removeprefix("0x").lower()


def _uint_word(value: int) -> str:
    return f"{value:064x}"


def _creation_input() -> str:
    words = (
        _address_word("0x1111111111111111111111111111111111111111"),
        _address_word("0x2222222222222222222222222222222222222222"),
        _address_word("0x3333333333333333333333333333333333333333"),
        _uint_word(300),
        _uint_word(30),
        _uint_word(10**15),
        _uint_word(300),
        _uint_word(300),
    )
    return "0x6000600055" + "".join(words)


def test_decode_prediction_v2_constructor_input_uses_static_tail() -> None:
    input_data = _creation_input()
    decoded = decode_prediction_v2_constructor_input(input_data)
    expected_hash = hashlib.sha256(bytes.fromhex(input_data[2:])).hexdigest()

    assert decoded["creation_input_sha256"] == expected_hash
    assert decoded["oracle_address"] == "0x1111111111111111111111111111111111111111"
    assert decoded["admin_address"] == "0x2222222222222222222222222222222222222222"
    assert decoded["operator_address"] == "0x3333333333333333333333333333333333333333"
    assert decoded["interval_seconds"] == 300
    assert decoded["buffer_seconds"] == 30
    assert decoded["min_bet_amount_wei"] == 10**15
    assert decoded["oracle_update_allowance_seconds"] == 300
    assert decoded["treasury_fee_bps"] == 300


def test_decode_creation_transaction_binds_verified_market_metadata() -> None:
    market = MARKETS["BNBUSD"]
    assert market.creation_tx_hash is not None
    transaction = {
        "hash": market.creation_tx_hash,
        "to": None,
        "blockNumber": hex(10_333_825),
        "input": _creation_input(),
    }
    decoded = decode_prediction_v2_creation_transaction(transaction, market)

    assert decoded.market == "BNBUSD"
    assert decoded.block_number == 10_333_825
    assert decoded.transaction_hash == market.creation_tx_hash
    assert decoded.contract_address == market.address.lower()
    assert decoded.oracle_address == "0x1111111111111111111111111111111111111111"


def test_creation_transaction_rejects_metadata_mismatch() -> None:
    market = Market(
        "TEST",
        "0x4444444444444444444444444444444444444444",
        123,
        "0x" + "ab" * 32,
    )
    transaction = {
        "hash": "0x" + "cd" * 32,
        "to": None,
        "blockNumber": hex(123),
        "input": _creation_input(),
    }
    with pytest.raises(DeploymentProvenanceError, match="hash does not match"):
        decode_prediction_v2_creation_transaction(transaction, market)


def test_constructor_decoder_rejects_non_static_short_input() -> None:
    with pytest.raises(DeploymentProvenanceError, match="shorter"):
        decode_prediction_v2_constructor_input("0x1234")
