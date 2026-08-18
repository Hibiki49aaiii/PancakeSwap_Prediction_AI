from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .abi import encode_bet_calldata
from .contracts import CHAIN_ID_BSC, market


def normalize_evm_address(value: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized) != 42:
        raise ValueError("invalid EVM address")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError("invalid EVM address") from exc
    return normalized


@dataclass(frozen=True, slots=True)
class UnsignedBetIntent:
    chain_id: int
    wallet_address: str
    market: str
    epoch: int
    side: str
    target_address: str
    value_wei: int
    data_hex: str
    semantic_payload_hash: str

    def canonical_dict(self) -> dict[str, int | str]:
        result = asdict(self)
        result.pop("semantic_payload_hash")
        return result


def _semantic_hash(payload: dict[str, int | str]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


def build_unsigned_bet(
    *,
    wallet_address: str,
    market_symbol: str,
    epoch: int,
    side: str,
    value_wei: int,
    chain_id: int = CHAIN_ID_BSC,
) -> UnsignedBetIntent:
    if chain_id != CHAIN_ID_BSC:
        raise ValueError(f"unsupported chain id: {chain_id}")
    if value_wei <= 0:
        raise ValueError("value_wei must be positive")

    market_config = market(market_symbol)
    normalized_side = side.lower()
    wallet = normalize_evm_address(wallet_address)
    data_hex = encode_bet_calldata(normalized_side, epoch)

    payload: dict[str, int | str] = {
        "chain_id": chain_id,
        "wallet_address": wallet,
        "market": market_config.symbol,
        "epoch": epoch,
        "side": normalized_side,
        "target_address": market_config.address,
        "value_wei": value_wei,
        "data_hex": data_hex,
    }
    return UnsignedBetIntent(**payload, semantic_payload_hash=_semantic_hash(payload))
