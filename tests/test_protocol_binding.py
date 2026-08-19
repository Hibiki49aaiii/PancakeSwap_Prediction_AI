from __future__ import annotations

import pytest
from eth_abi import encode

from pancake_prediction_ai.abi_codec import function_selector
from pancake_prediction_ai.pancake_contract import BNB_PREDICTION_CONTRACT
from pancake_prediction_ai.protocol_binding import discover_bnb_prediction_binding


ORACLE = "0x2222222222222222222222222222222222222222"


def _address_result(address: str) -> str:
    return "0x" + encode(["address"], [address]).hex()


class FakeRpc:
    def __init__(self, *, chain_id: int = 56, oracle: str = ORACLE) -> None:
        self.chain_id = chain_id
        self.oracle = oracle

    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_call":
            tx = params[0]
            assert isinstance(tx, dict)
            assert str(tx["to"]).lower() == BNB_PREDICTION_CONTRACT.lower()
            assert bytes.fromhex(str(tx["data"])[2:]) == function_selector("oracle()")
            assert params[1] == "latest"
            return _address_result(self.oracle)
        raise KeyError(method)


def test_discovers_active_oracle_from_canonical_prediction_contract() -> None:
    binding = discover_bnb_prediction_binding(FakeRpc())
    assert binding.chain_id == 56
    assert binding.prediction_contract == BNB_PREDICTION_CONTRACT.lower()
    assert binding.chainlink_oracle == ORACLE
    assert binding.valid_for_bnb_prediction


def test_rejects_non_bsc_rpc() -> None:
    with pytest.raises(ValueError, match="chainId 56"):
        discover_bnb_prediction_binding(FakeRpc(chain_id=1))


def test_rejects_noncanonical_prediction_target() -> None:
    with pytest.raises(ValueError, match="canonical"):
        discover_bnb_prediction_binding(
            FakeRpc(),
            prediction_contract="0x3333333333333333333333333333333333333333",
        )


def test_rejects_zero_oracle_address() -> None:
    with pytest.raises(ValueError, match="invalid address"):
        discover_bnb_prediction_binding(
            FakeRpc(oracle="0x0000000000000000000000000000000000000000")
        )
