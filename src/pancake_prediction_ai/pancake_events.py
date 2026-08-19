from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .abi_codec import decode_result
from .event_store import EventRecord
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT
from .read_only_rpc import ReadOnlyJsonRpcClient, RpcError
from .rpc_snapshot import BlockAnchor, fetch_block_anchor_by_number
from eth_hash.auto import keccak


ClockNs = Callable[[], int]


class LifecycleKind(StrEnum):
    START = "START"
    LOCK = "LOCK"
    END = "END"


_EVENT_SIGNATURES = {
    LifecycleKind.START: "StartRound(uint256)",
    LifecycleKind.LOCK: "LockRound(uint256,uint256,int256)",
    LifecycleKind.END: "EndRound(uint256,uint256,int256)",
}
_EVENT_TOPICS = {
    kind: "0x" + keccak(signature.encode("ascii")).hex()
    for kind, signature in _EVENT_SIGNATURES.items()
}
_TOPIC_TO_KIND = {topic.lower(): kind for kind, topic in _EVENT_TOPICS.items()}


@dataclass(frozen=True, slots=True)
class RoundLifecycleObservation:
    kind: LifecycleKind
    epoch: int
    block_number: int
    block_hash: str
    block_timestamp_s: int
    transaction_hash: str
    log_index: int
    oracle_round_id: int | None
    price: int | None
    event: EventRecord


def lifecycle_topic(kind: LifecycleKind) -> str:
    return _EVENT_TOPICS[kind]


def _hex_int(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"{field} must be a hex string")
    return int(value, 16)


def _hex_bytes32(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        raise ValueError(f"{field} must be 32-byte hex")
    try:
        int(value[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{field} must be hex") from exc
    return value.lower()


def _decode_indexed_uint(topic: object, field: str) -> int:
    return _hex_int(topic, field)


def _normalize_log(
    raw: dict[str, Any],
    *,
    anchor: BlockAnchor,
    observed_at_ns: int,
    prediction_contract: str,
) -> RoundLifecycleObservation:
    if raw.get("removed") is True:
        raise ValueError("removed/reorged lifecycle log cannot be canonicalized")
    address = str(raw.get("address", "")).lower()
    if address != prediction_contract.lower():
        raise ValueError("lifecycle log address does not match Prediction contract")
    topics = raw.get("topics")
    if not isinstance(topics, list) or len(topics) < 2:
        raise ValueError("lifecycle log topics are incomplete")
    topic0 = str(topics[0]).lower()
    kind = _TOPIC_TO_KIND.get(topic0)
    if kind is None:
        raise ValueError("unsupported lifecycle topic")

    block_number = _hex_int(raw.get("blockNumber"), "blockNumber")
    if block_number != anchor.number:
        raise ValueError("log block number does not match fetched block anchor")
    block_hash = _hex_bytes32(raw.get("blockHash"), "blockHash")
    if block_hash != anchor.block_hash.lower():
        raise ValueError("log block hash does not match fetched block anchor")
    tx_hash = _hex_bytes32(raw.get("transactionHash"), "transactionHash")
    log_index = _hex_int(raw.get("logIndex"), "logIndex")
    epoch = _decode_indexed_uint(topics[1], "epoch topic")

    oracle_round_id: int | None = None
    price: int | None = None
    data = raw.get("data")
    if not isinstance(data, str) or not data.startswith("0x"):
        raise ValueError("lifecycle log data must be hex")

    if kind is LifecycleKind.START:
        if len(topics) != 2:
            raise ValueError("StartRound must contain exactly epoch indexed topic")
        if data != "0x":
            # Solidity encodes no non-indexed values for StartRound.
            if bytes.fromhex(data[2:]):
                raise ValueError("StartRound contains unexpected data")
    else:
        if len(topics) != 3:
            raise ValueError(f"{kind.value} must contain epoch and roundId topics")
        oracle_round_id = _decode_indexed_uint(topics[2], "roundId topic")
        decoded = decode_result(data, ("int256",))
        price = int(decoded[0])

    event = EventRecord(
        event_id=f"pancake:prediction:lifecycle:{tx_hash}:{log_index}",
        source="pancake_prediction",
        topic="prediction.round_lifecycle",
        event_time_ns=anchor.timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "contract_address": prediction_contract.lower(),
            "kind": kind.value,
            "epoch": epoch,
            "block_number": anchor.number,
            "block_hash": anchor.block_hash.lower(),
            "block_timestamp_s": anchor.timestamp_s,
            "transaction_hash": tx_hash,
            "log_index": log_index,
            "oracle_round_id": oracle_round_id,
            "price": price,
        },
    )
    return RoundLifecycleObservation(
        kind=kind,
        epoch=epoch,
        block_number=anchor.number,
        block_hash=anchor.block_hash.lower(),
        block_timestamp_s=anchor.timestamp_s,
        transaction_hash=tx_hash,
        log_index=log_index,
        oracle_round_id=oracle_round_id,
        price=price,
        event=event,
    )


def collect_round_lifecycle_logs(
    client: ReadOnlyJsonRpcClient,
    *,
    from_block: int,
    to_block: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> tuple[RoundLifecycleObservation, ...]:
    """Collect Start/Lock/End logs and attach canonical block timestamps."""

    if from_block < 0 or to_block < from_block:
        raise ValueError("invalid log block range")
    chain_id = client.chain_id()
    if chain_id != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}, got {chain_id}")

    result = client.call(
        "eth_getLogs",
        [
            {
                "address": prediction_contract,
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [[
                    lifecycle_topic(LifecycleKind.START),
                    lifecycle_topic(LifecycleKind.LOCK),
                    lifecycle_topic(LifecycleKind.END),
                ]],
            }
        ],
    )
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise RpcError("eth_getLogs lifecycle response must be an array of objects")

    block_numbers = sorted({_hex_int(item.get("blockNumber"), "blockNumber") for item in result})
    anchors = {
        number: fetch_block_anchor_by_number(client, number)
        for number in block_numbers
    }
    observed_at_ns = clock_ns()
    if observed_at_ns < 0:
        raise ValueError("clock returned negative observation time")

    observations = tuple(
        sorted(
            (
                _normalize_log(
                    item,
                    anchor=anchors[_hex_int(item.get("blockNumber"), "blockNumber")],
                    observed_at_ns=observed_at_ns,
                    prediction_contract=prediction_contract,
                )
                for item in result
            ),
            key=lambda item: (item.block_number, item.log_index),
        )
    )
    return observations


def lifecycle_events(observations: Iterable[RoundLifecycleObservation]) -> tuple[EventRecord, ...]:
    return tuple(observation.event for observation in observations)
