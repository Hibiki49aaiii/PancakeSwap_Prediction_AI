from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .abi_codec import decode_result, encode_call
from .pancake_contract import BNB_PREDICTION_CONTRACT


RpcCall = Callable[[str, list[Any]], Any]


@dataclass(frozen=True, slots=True)
class ProtocolBinding:
    chain_id: int
    prediction_contract: str
    chainlink_oracle: str

    @property
    def valid_for_bnb_prediction(self) -> bool:
        return (
            self.chain_id == 56
            and self.prediction_contract.lower() == BNB_PREDICTION_CONTRACT.lower()
            and _is_address(self.chainlink_oracle)
            and self.chainlink_oracle.lower() != "0x" + "0" * 40
        )


def _hex_int(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("RPC integer result must be hex string")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ValueError("RPC integer result must be hexadecimal") from exc


def _is_address(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 42 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


def discover_bnb_prediction_binding(
    rpc: RpcCall,
    *,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> ProtocolBinding:
    if not _is_address(prediction_contract):
        raise ValueError("prediction_contract must be a 20-byte hex address")
    if prediction_contract.lower() != BNB_PREDICTION_CONTRACT.lower():
        raise ValueError("prediction_contract must equal canonical BNB Prediction contract")

    chain_id = _hex_int(rpc("eth_chainId", []))
    if chain_id != 56:
        raise ValueError(f"expected BSC chainId 56, got {chain_id}")

    raw = rpc(
        "eth_call",
        [
            {
                "to": prediction_contract,
                "data": encode_call("oracle()"),
            },
            "latest",
        ],
    )
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise ValueError("Prediction oracle() call must return hex data")
    decoded = decode_result(raw, ("address",))
    oracle = str(decoded[0]).lower()
    if not _is_address(oracle) or oracle == "0x" + "0" * 40:
        raise ValueError("Prediction oracle() returned an invalid address")

    binding = ProtocolBinding(
        chain_id=chain_id,
        prediction_contract=prediction_contract.lower(),
        chainlink_oracle=oracle,
    )
    if not binding.valid_for_bnb_prediction:
        raise ValueError("discovered protocol binding is invalid")
    return binding
