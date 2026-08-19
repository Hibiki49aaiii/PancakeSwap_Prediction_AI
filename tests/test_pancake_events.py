from __future__ import annotations

from eth_abi import encode
import pytest

from pancake_prediction_ai.pancake_contract import BNB_PREDICTION_CONTRACT
from pancake_prediction_ai.pancake_events import (
    LifecycleKind,
    collect_round_lifecycle_logs,
    lifecycle_topic,
)


def _topic_uint(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _data_int(value: int) -> str:
    return "0x" + encode(["int256"], [value]).hex()


def _block_hash(number: int) -> str:
    return "0x" + f"{number:064x}"


def _tx_hash(number: int) -> str:
    return "0x" + f"{number + 1000:064x}"


def _log(kind: LifecycleKind, *, epoch: int, block: int, log_index: int, price=None, oracle_round_id=None):
    topics = [lifecycle_topic(kind), _topic_uint(epoch)]
    data = "0x"
    if kind is not LifecycleKind.START:
        topics.append(_topic_uint(int(oracle_round_id)))
        data = _data_int(int(price))
    return {
        "address": BNB_PREDICTION_CONTRACT,
        "topics": topics,
        "data": data,
        "blockNumber": hex(block),
        "blockHash": _block_hash(block),
        "transactionHash": _tx_hash(block),
        "logIndex": hex(log_index),
        "removed": False,
    }


class FakeRpc:
    def __init__(self, logs, *, chain_id=56):
        self.logs = logs
        self._chain_id = chain_id
        self.filters = []

    def chain_id(self):
        return self._chain_id

    def call(self, method, params):
        if method == "eth_getLogs":
            self.filters.append(params[0])
            return self.logs
        if method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            return {
                "number": hex(number),
                "hash": _block_hash(number),
                "timestamp": hex(1_000 + number * 3),
            }
        raise AssertionError(method)


def test_lifecycle_logs_are_sorted_timestamped_and_decoded() -> None:
    logs = [
        _log(LifecycleKind.END, epoch=7, block=12, log_index=2, oracle_round_id=102, price=601_00000000),
        _log(LifecycleKind.START, epoch=7, block=10, log_index=0),
        _log(LifecycleKind.LOCK, epoch=7, block=11, log_index=1, oracle_round_id=101, price=600_00000000),
    ]
    client = FakeRpc(logs)
    observations = collect_round_lifecycle_logs(
        client,  # type: ignore[arg-type]
        from_block=10,
        to_block=12,
        clock_ns=lambda: 9_999_000_000_000,
    )
    assert [item.kind for item in observations] == [
        LifecycleKind.START,
        LifecycleKind.LOCK,
        LifecycleKind.END,
    ]
    start, lock, end = observations
    assert start.epoch == lock.epoch == end.epoch == 7
    assert lock.oracle_round_id == 101
    assert lock.price == 600_00000000
    assert end.oracle_round_id == 102
    assert end.price == 601_00000000
    assert lock.event.event_time_ns == (1_000 + 11 * 3) * 1_000_000_000
    assert {item.event.observed_at_ns for item in observations} == {9_999_000_000_000}

    event_filter = client.filters[0]
    assert event_filter["fromBlock"] == "0xa"
    assert event_filter["toBlock"] == "0xc"
    assert event_filter["address"] == BNB_PREDICTION_CONTRACT
    assert set(event_filter["topics"][0]) == {
        lifecycle_topic(LifecycleKind.START),
        lifecycle_topic(LifecycleKind.LOCK),
        lifecycle_topic(LifecycleKind.END),
    }


def test_removed_log_is_rejected_as_noncanonical() -> None:
    log = _log(LifecycleKind.START, epoch=7, block=10, log_index=0)
    log["removed"] = True
    with pytest.raises(ValueError, match="removed/reorged"):
        collect_round_lifecycle_logs(
            FakeRpc([log]),  # type: ignore[arg-type]
            from_block=10,
            to_block=10,
            clock_ns=lambda: 1,
        )


def test_wrong_chain_rejected_before_log_query() -> None:
    client = FakeRpc([], chain_id=1)
    with pytest.raises(ValueError, match="expected BNB chain id 56"):
        collect_round_lifecycle_logs(
            client,  # type: ignore[arg-type]
            from_block=1,
            to_block=2,
        )
    assert client.filters == []
