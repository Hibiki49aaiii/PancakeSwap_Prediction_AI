from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .abi_codec import decode_result, encode_call
from .event_store import EventRecord
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT, PredictionRoundState
from .read_only_rpc import ReadOnlyJsonRpcClient
from .rpc_snapshot import (
    BlockAnchor,
    eth_call_at,
    fetch_block_anchor,
    fetch_block_anchor_by_number,
    get_code_at,
)
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


def _validate_chain(client: ReadOnlyJsonRpcClient) -> None:
    chain_id = client.chain_id()
    if chain_id != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}, got {chain_id}")


def _single_uint(
    client: ReadOnlyJsonRpcClient,
    anchor: BlockAnchor,
    contract: str,
    signature: str,
) -> int:
    raw = eth_call_at(client, to=contract, data=encode_call(signature), anchor=anchor)
    values = decode_result(raw, ("uint256",))
    return int(values[0])


def _address_call(
    client: ReadOnlyJsonRpcClient,
    anchor: BlockAnchor,
    contract: str,
    signature: str,
) -> str:
    raw = eth_call_at(client, to=contract, data=encode_call(signature), anchor=anchor)
    values = decode_result(raw, ("address",))
    address = str(values[0]).lower()
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"{signature} returned invalid address")
    return address


def read_prediction_round_at_anchor(
    client: ReadOnlyJsonRpcClient,
    *,
    anchor: BlockAnchor,
    epoch: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> PredictionRoundState:
    """Read one explicit Prediction epoch at an already pinned BSC block."""

    anchor.validate()
    if epoch < 0:
        raise ValueError("epoch must be non-negative")
    round_raw = eth_call_at(
        client,
        to=prediction_contract,
        data=encode_call(
            "rounds(uint256)",
            argument_types=("uint256",),
            arguments=(epoch,),
        ),
        anchor=anchor,
    )
    values = decode_result(round_raw, ROUND_OUTPUT_TYPES)
    state = PredictionRoundState(
        epoch=int(values[0]),
        start_timestamp=int(values[1]),
        lock_timestamp=int(values[2]),
        close_timestamp=int(values[3]),
        lock_price=int(values[4]),
        close_price=int(values[5]),
        lock_oracle_id=int(values[6]),
        close_oracle_id=int(values[7]),
        total_amount_wei=int(values[8]),
        bull_amount_wei=int(values[9]),
        bear_amount_wei=int(values[10]),
        reward_base_cal_amount_wei=int(values[11]),
        reward_amount_wei=int(values[12]),
        oracle_called=bool(values[13]),
    )
    state.validate()
    if state.start_timestamp != 0 and state.epoch != epoch:
        raise ValueError(
            f"rounds({epoch}) returned a different epoch: {state.epoch}"
        )
    return state


def read_prediction_buffer_seconds_at_anchor(
    client: ReadOnlyJsonRpcClient,
    *,
    anchor: BlockAnchor,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> int:
    """Read the buffer used by the contract's current refundability condition."""

    value = _single_uint(client, anchor, prediction_contract, "bufferSeconds()")
    if value < 0:
        raise AssertionError("bufferSeconds decoded negative")
    return value


def collect_protocol_snapshot_at_anchor(
    client: ReadOnlyJsonRpcClient,
    *,
    anchor: BlockAnchor,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> PinnedProtocolSnapshot:
    """Read Pancake Prediction and its active Chainlink feed at one anchor."""

    anchor.validate()
    prediction_code = get_code_at(client, address=prediction_contract, anchor=anchor)
    if prediction_code in {"0x", "0x0", ""}:
        raise ValueError("Prediction contract has no code at pinned block")

    current_epoch = _single_uint(client, anchor, prediction_contract, "currentEpoch()")
    treasury_fee_units = _single_uint(client, anchor, prediction_contract, "treasuryFee()")
    oracle_address = _address_call(client, anchor, prediction_contract, "oracle()")
    round_state = read_prediction_round_at_anchor(
        client,
        anchor=anchor,
        epoch=current_epoch,
        prediction_contract=prediction_contract,
    )
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


def collect_pinned_protocol_snapshot(
    client: ReadOnlyJsonRpcClient,
    *,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> PinnedProtocolSnapshot:
    """Read the current protocol state at one concrete BSC block."""

    _validate_chain(client)
    anchor = fetch_block_anchor(client)
    return collect_protocol_snapshot_at_anchor(
        client,
        anchor=anchor,
        prediction_contract=prediction_contract,
        clock_ns=clock_ns,
    )


def collect_protocol_snapshot_at_block(
    client: ReadOnlyJsonRpcClient,
    *,
    block_number: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> PinnedProtocolSnapshot:
    """Read historical protocol state at an explicit BSC block number."""

    _validate_chain(client)
    anchor = fetch_block_anchor_by_number(client, block_number)
    return collect_protocol_snapshot_at_anchor(
        client,
        anchor=anchor,
        prediction_contract=prediction_contract,
        clock_ns=clock_ns,
    )
