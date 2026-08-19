from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .abi_codec import decode_result, encode_call
from .event_store import EventRecord
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT, PredictionRoundState
from .read_only_rpc import ReadOnlyJsonRpcClient
from .rpc_snapshot import BlockAnchor, eth_call_at, fetch_block_anchor, get_code_at
from .sources.chainlink import normalize_latest_round_data
from .sources.pancake import normalize_oracle_reference, normalize_round_snapshot


ClockNs = Callable[[], int]


ROUND_OUTPUT_TYPES = (
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
)


@dataclass(frozen=True, slots=True)
class PinnedProtocolSnapshot:
    anchor: BlockAnchor
    current_epoch: int
    treasury_fee_units: int
    oracle_address: str
    oracle_decimals: int
    oracle_description: str
    round_state: PredictionRoundState
    events: tuple[EventRecord, ...]

    def validate(self) -> None:
        self.anchor.validate()
        self.round_state.validate()
        if self.current_epoch != self.round_state.epoch:
            raise ValueError("current epoch and round tuple epoch disagree")
        if not 0 <= self.treasury_fee_units <= 1000:
            raise ValueError("treasury fee outside Prediction contract bound")
        if not 0 <= self.oracle_decimals <= 36:
            raise ValueError("oracle decimals outside supported bound")
        if len(self.events) != 3:
            raise ValueError("protocol snapshot must contain three canonical events")
        if len({event.observed_at_ns for event in self.events}) != 1:
            raise ValueError("protocol snapshot events must share one observation timestamp")


def _single_uint(client: ReadOnlyJsonRpcClient, anchor: BlockAnchor, contract: str, signature: str) -> int:
    raw = eth_call_at(client, to=contract, data=encode_call(signature), anchor=anchor)
    values = decode_result(raw, ("uint256",))
    return int(values[0])


def _address_call(client: ReadOnlyJsonRpcClient, anchor: BlockAnchor, contract: str, signature: str) -> str:
    raw = eth_call_at(client, to=contract, data=encode_call(signature), anchor=anchor)
    values = decode_result(raw, ("address",))
    address = str(values[0]).lower()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"{signature} returned invalid address")
    return address


def collect_pinned_protocol_snapshot(
    client: ReadOnlyJsonRpcClient,
    *,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> PinnedProtocolSnapshot:
    """Read Pancake Prediction + active Chainlink feed at one concrete BSC block.

    All view calls use the same numeric block tag. `observed_at_ns` is sampled
    only after every required RPC response is available, making the resulting
    canonical events conservative with respect to information availability.
    """

    chain_id = client.chain_id()
    if chain_id != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}, got {chain_id}")

    anchor = fetch_block_anchor(client)
    prediction_code = get_code_at(client, address=prediction_contract, anchor=anchor)
    if prediction_code in {"0x", "0x0", ""}:
        raise ValueError("Prediction contract has no code at pinned block")

    current_epoch = _single_uint(client, anchor, prediction_contract, "currentEpoch()")
    treasury_fee_units = _single_uint(client, anchor, prediction_contract, "treasuryFee()")
    oracle_address = _address_call(client, anchor, prediction_contract, "oracle()")

    round_raw = eth_call_at(
        client,
        to=prediction_contract,
        data=encode_call(
            "rounds(uint256)",
            argument_types=("uint256",),
            arguments=(current_epoch,),
        ),
        anchor=anchor,
    )
    round_values = decode_result(round_raw, ROUND_OUTPUT_TYPES)
    round_state = PredictionRoundState(
        epoch=int(round_values[0]),
        start_timestamp=int(round_values[1]),
        lock_timestamp=int(round_values[2]),
        close_timestamp=int(round_values[3]),
        lock_price=int(round_values[4]),
        close_price=int(round_values[5]),
        lock_oracle_id=int(round_values[6]),
        close_oracle_id=int(round_values[7]),
        total_amount_wei=int(round_values[8]),
        bull_amount_wei=int(round_values[9]),
        bear_amount_wei=int(round_values[10]),
        reward_base_cal_amount_wei=int(round_values[11]),
        reward_amount_wei=int(round_values[12]),
        oracle_called=bool(round_values[13]),
    )
    round_state.validate()
    if round_state.epoch != current_epoch:
        raise ValueError("rounds(currentEpoch) returned a different epoch")

    oracle_code = get_code_at(client, address=oracle_address, anchor=anchor)
    if oracle_code in {"0x", "0x0", ""}:
        raise ValueError("Prediction oracle has no code at pinned block")

    decimals_raw = eth_call_at(
        client,
        to=oracle_address,
        data=encode_call("decimals()"),
        anchor=anchor,
    )
    oracle_decimals = int(decode_result(decimals_raw, ("uint8",))[0])

    description_raw = eth_call_at(
        client,
        to=oracle_address,
        data=encode_call("description()"),
        anchor=anchor,
    )
    oracle_description = str(decode_result(description_raw, ("string",))[0])

    latest_raw = eth_call_at(
        client,
        to=oracle_address,
        data=encode_call("latestRoundData()"),
        anchor=anchor,
    )
    latest_values = decode_result(
        latest_raw,
        ("uint80", "int256", "uint256", "uint256", "uint80"),
    )

    observed_at_ns = clock_ns()
    if observed_at_ns < 0:
        raise ValueError("clock returned a negative observation time")

    round_event = normalize_round_snapshot(
        round_state,
        contract_address=prediction_contract,
        treasury_fee_units=treasury_fee_units,
        block_number=anchor.number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=observed_at_ns,
    )
    oracle_reference_event = normalize_oracle_reference(
        oracle_address,
        contract_address=prediction_contract,
        block_number=anchor.number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=observed_at_ns,
    )
    chainlink_event = normalize_latest_round_data(
        latest_values,
        decimals=oracle_decimals,
        feed_address=oracle_address,
        observed_at_ns=observed_at_ns,
        description=oracle_description,
    )

    snapshot = PinnedProtocolSnapshot(
        anchor=anchor,
        current_epoch=current_epoch,
        treasury_fee_units=treasury_fee_units,
        oracle_address=oracle_address,
        oracle_decimals=oracle_decimals,
        oracle_description=oracle_description,
        round_state=round_state,
        events=(round_event, oracle_reference_event, chainlink_event),
    )
    snapshot.validate()
    return snapshot
