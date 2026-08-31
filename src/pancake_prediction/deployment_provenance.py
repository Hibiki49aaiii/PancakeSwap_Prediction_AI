from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import cast

from .contracts import Market

_STATIC_CONSTRUCTOR_WORDS = 8
_WORD_HEX_LENGTH = 64
_ADDRESS_PREFIX_HEX_LENGTH = 24


class DeploymentProvenanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PredictionDeploymentProvenance:
    market: str
    contract_address: str
    transaction_hash: str
    block_number: int
    creation_input_sha256: str
    oracle_address: str
    admin_address: str
    operator_address: str
    interval_seconds: int
    buffer_seconds: int
    min_bet_amount_wei: int
    oracle_update_allowance_seconds: int
    treasury_fee_bps: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _decode_address_word(word: str, *, field: str) -> str:
    if len(word) != _WORD_HEX_LENGTH:
        raise DeploymentProvenanceError(f"{field} constructor word must be 32 bytes")
    if any(char not in "0123456789abcdef" for char in word):
        raise DeploymentProvenanceError(f"{field} constructor word is not hex")
    if int(word[:_ADDRESS_PREFIX_HEX_LENGTH], 16) != 0:
        raise DeploymentProvenanceError(f"{field} constructor address is not ABI padded")
    address = "0x" + word[_ADDRESS_PREFIX_HEX_LENGTH:]
    if int(address[2:], 16) == 0:
        raise DeploymentProvenanceError(f"{field} constructor address is zero")
    return address


def _decode_uint_word(word: str, *, field: str) -> int:
    if len(word) != _WORD_HEX_LENGTH:
        raise DeploymentProvenanceError(f"{field} constructor word must be 32 bytes")
    try:
        return int(word, 16)
    except ValueError as exc:
        raise DeploymentProvenanceError(f"{field} constructor word is not hex") from exc


def decode_prediction_v2_constructor_input(input_data: str) -> dict[str, object]:
    if not input_data.startswith("0x"):
        raise DeploymentProvenanceError("creation input must be 0x-prefixed")
    payload = input_data[2:].lower()
    constructor_hex_length = _STATIC_CONSTRUCTOR_WORDS * _WORD_HEX_LENGTH
    if len(payload) < constructor_hex_length:
        raise DeploymentProvenanceError("creation input is shorter than constructor arguments")
    if len(payload) % 2 != 0:
        raise DeploymentProvenanceError("creation input must contain whole bytes")
    try:
        raw_input = bytes.fromhex(payload)
    except ValueError as exc:
        raise DeploymentProvenanceError("creation input contains non-hex data") from exc

    arguments = payload[-constructor_hex_length:]
    words = tuple(
        arguments[offset : offset + _WORD_HEX_LENGTH]
        for offset in range(0, constructor_hex_length, _WORD_HEX_LENGTH)
    )
    oracle = _decode_address_word(words[0], field="oracle")
    admin = _decode_address_word(words[1], field="admin")
    operator = _decode_address_word(words[2], field="operator")
    interval = _decode_uint_word(words[3], field="intervalSeconds")
    buffer_seconds = _decode_uint_word(words[4], field="bufferSeconds")
    min_bet = _decode_uint_word(words[5], field="minBetAmount")
    allowance = _decode_uint_word(words[6], field="oracleUpdateAllowance")
    treasury_fee = _decode_uint_word(words[7], field="treasuryFee")

    if interval <= 0:
        raise DeploymentProvenanceError("constructor intervalSeconds must be positive")
    if treasury_fee > 1_000:
        raise DeploymentProvenanceError("constructor treasuryFee exceeds Prediction V2 maximum")

    return {
        "creation_input_sha256": hashlib.sha256(raw_input).hexdigest(),
        "oracle_address": oracle,
        "admin_address": admin,
        "operator_address": operator,
        "interval_seconds": interval,
        "buffer_seconds": buffer_seconds,
        "min_bet_amount_wei": min_bet,
        "oracle_update_allowance_seconds": allowance,
        "treasury_fee_bps": treasury_fee,
    }


def decode_prediction_v2_creation_transaction(
    transaction: Mapping[str, object],
    market: Market,
) -> PredictionDeploymentProvenance:
    if market.creation_tx_hash is None or market.deployment_block_hint is None:
        raise DeploymentProvenanceError(
            f"{market.symbol} does not have verified deployment metadata"
        )
    transaction_hash = transaction.get("hash")
    if not isinstance(transaction_hash, str):
        raise DeploymentProvenanceError("creation transaction is missing hash")
    if transaction_hash.lower() != market.creation_tx_hash.lower():
        raise DeploymentProvenanceError("creation transaction hash does not match market metadata")
    if transaction.get("to") is not None:
        raise DeploymentProvenanceError("expected a contract-creation transaction")

    block_raw = transaction.get("blockNumber")
    if not isinstance(block_raw, str):
        raise DeploymentProvenanceError("creation transaction is missing blockNumber")
    try:
        block_number = int(block_raw, 16)
    except ValueError as exc:
        raise DeploymentProvenanceError("creation transaction blockNumber is invalid") from exc
    if block_number != market.deployment_block_hint:
        raise DeploymentProvenanceError("creation transaction block does not match market metadata")

    input_data = transaction.get("input")
    if not isinstance(input_data, str):
        raise DeploymentProvenanceError("creation transaction is missing input")
    decoded = decode_prediction_v2_constructor_input(input_data)
    return PredictionDeploymentProvenance(
        market=market.symbol,
        contract_address=market.address.lower(),
        transaction_hash=transaction_hash.lower(),
        block_number=block_number,
        creation_input_sha256=cast(str, decoded["creation_input_sha256"]),
        oracle_address=cast(str, decoded["oracle_address"]),
        admin_address=cast(str, decoded["admin_address"]),
        operator_address=cast(str, decoded["operator_address"]),
        interval_seconds=cast(int, decoded["interval_seconds"]),
        buffer_seconds=cast(int, decoded["buffer_seconds"]),
        min_bet_amount_wei=cast(int, decoded["min_bet_amount_wei"]),
        oracle_update_allowance_seconds=cast(
            int,
            decoded["oracle_update_allowance_seconds"],
        ),
        treasury_fee_bps=cast(int, decoded["treasury_fee_bps"]),
    )
