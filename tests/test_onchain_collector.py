from __future__ import annotations

from eth_abi import encode

import pytest

from pancake_prediction_ai.abi_codec import encode_call, function_selector
from pancake_prediction_ai.onchain_collector import ROUND_OUTPUT_TYPES, collect_pinned_protocol_snapshot
from pancake_prediction_ai.pancake_contract import BNB_PREDICTION_CONTRACT


ORACLE = "0x1111111111111111111111111111111111111111"


def _hex(types, values) -> str:
    return "0x" + encode(list(types), list(values)).hex()


class FakeRpc:
    def __init__(self, *, chain_id: int = 56, round_epoch: int = 7) -> None:
        self._chain_id = chain_id
        self._round_epoch = round_epoch
        self.calls: list[tuple[str, list[object]]] = []

    def chain_id(self) -> int:
        return self._chain_id

    def block_number(self) -> int:
        return 100

    def call(self, method: str, params: list[object]):
        self.calls.append((method, params))
        if method == "eth_getBlockByNumber":
            assert params == ["0x64", False]
            return {"number": "0x64", "hash": "0x" + "ab" * 32, "timestamp": "0x3e8"}
        if method == "eth_getCode":
            assert params[1] == "0x64"
            return "0x60016000"
        if method != "eth_call":
            raise AssertionError(method)

        call = params[0]
        assert isinstance(call, dict)
        assert params[1] == "0x64"
        data = call["data"]
        to = str(call["to"]).lower()

        if data == encode_call("currentEpoch()"):
            return _hex(("uint256",), (7,))
        if data == encode_call("treasuryFee()"):
            return _hex(("uint256",), (300,))
        if data == encode_call("oracle()"):
            return _hex(("address",), (ORACLE,))
        if data == encode_call("rounds(uint256)", argument_types=("uint256",), arguments=(7,)):
            return _hex(
                ROUND_OUTPUT_TYPES,
                (
                    self._round_epoch,
                    900,
                    1100,
                    1300,
                    600_00000000,
                    0,
                    10,
                    0,
                    300,
                    120,
                    180,
                    0,
                    0,
                    False,
                ),
            )
        if to == ORACLE.lower() and data == encode_call("decimals()"):
            return _hex(("uint8",), (8,))
        if to == ORACLE.lower() and data == encode_call("description()"):
            return _hex(("string",), ("BNB / USD",))
        if to == ORACLE.lower() and data == encode_call("latestRoundData()"):
            return _hex(
                ("uint80", "int256", "uint256", "uint256", "uint80"),
                (123, 600_12345678, 990, 995, 123),
            )
        raise AssertionError((to, data))


def test_known_ethereum_function_selectors() -> None:
    assert function_selector("decimals()").hex() == "313ce567"
    assert function_selector("latestRoundData()").hex() == "feaf968c"


def test_protocol_collection_pins_every_view_to_one_block_and_one_observation_time() -> None:
    client = FakeRpc()
    snapshot = collect_pinned_protocol_snapshot(
        client,  # type: ignore[arg-type]
        clock_ns=lambda: 1_000_500_000_000,
    )

    assert snapshot.anchor.number == 100
    assert snapshot.anchor.timestamp_s == 1000
    assert snapshot.current_epoch == 7
    assert snapshot.treasury_fee_units == 300
    assert snapshot.oracle_address == ORACLE
    assert snapshot.oracle_decimals == 8
    assert snapshot.oracle_description == "BNB / USD"
    assert snapshot.round_state.bull_amount_wei == 120
    assert snapshot.round_state.bear_amount_wei == 180

    round_event, oracle_reference, chainlink = snapshot.events
    assert {event.observed_at_ns for event in snapshot.events} == {1_000_500_000_000}
    assert round_event.payload["treasury_fee_ppm"] == 30_000
    assert oracle_reference.payload["oracle_address"] == ORACLE
    assert chainlink.payload["price"] == pytest.approx(600.12345678)
    assert chainlink.event_time_ns == 995_000_000_000

    for method, params in client.calls:
        if method in {"eth_call", "eth_getCode"}:
            assert params[-1] == "0x64"


def test_wrong_chain_is_rejected_before_contract_reads() -> None:
    client = FakeRpc(chain_id=1)
    with pytest.raises(ValueError, match="expected BNB chain id 56"):
        collect_pinned_protocol_snapshot(client)  # type: ignore[arg-type]
    assert client.calls == []


def test_round_epoch_mismatch_is_rejected() -> None:
    client = FakeRpc(round_epoch=6)
    with pytest.raises(ValueError, match="different epoch"):
        collect_pinned_protocol_snapshot(client)  # type: ignore[arg-type]
