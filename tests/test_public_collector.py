from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.public_collector import PublicHistoricalCollector
from pancake_prediction.rpc import RpcError
from pancake_prediction.store import EventStore


def _block(hash_suffix: str = "aa") -> dict[str, Any]:
    return {
        "number": "0x64",
        "hash": "0x" + hash_suffix * 32,
        "parentHash": "0x" + "11" * 32,
        "timestamp": "0x1",
    }


def _log(topic: str, index: int, *, hash_suffix: str = "aa") -> dict[str, Any]:
    return {
        "address": "0x" + "22" * 20,
        "blockNumber": "0x64",
        "blockHash": "0x" + hash_suffix * 32,
        "transactionHash": "0x" + f"{index + 1:064x}",
        "transactionIndex": hex(index),
        "logIndex": hex(index),
        "topics": [topic],
        "data": "0x",
    }


class TopicLimitRpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...] | None] = []
        self.last_topics: tuple[str, ...] | None = None

    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return 100

    def block(self, number: int) -> dict[str, Any]:
        assert number == 100
        return _block()

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        assert address
        assert from_block == to_block == 100
        self.calls.append(topic0s)
        self.last_topics = topic0s
        if topic0s is None or len(topic0s) > 1:
            raise RpcError("eth_getLogs: -32005 limit exceeded")
        topic = topic0s[0]
        index = int(topic[-1], 16)
        return [_log(topic, index)]

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        return "0x" + "00" * 12 + "33" * 20


class SingleTopicLimitRpc(TopicLimitRpc):
    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(topic0s)
        raise RpcError("eth_getLogs: -32005 limit exceeded")


class PartitionForkRpc(TopicLimitRpc):
    def block(self, number: int) -> dict[str, Any]:
        assert number == 100
        if self.last_topics == ("0x2",):
            return _block("bb")
        return _block("aa")

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        assert from_block == to_block == 100
        self.calls.append(topic0s)
        self.last_topics = topic0s
        if topic0s is None or len(topic0s) > 1:
            raise RpcError("eth_getLogs: -32005 limit exceeded")
        suffix = "bb" if topic0s == ("0x2",) else "aa"
        return [_log(topic0s[0], int(topic0s[0][-1], 16), hash_suffix=suffix)]


def _collector(tmp_path: Path, rpc: TopicLimitRpc) -> PublicHistoricalCollector:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    return PublicHistoricalCollector(rpc=rpc, store=store, consistency_retries=1)


def test_public_collector_splits_single_block_topic_overload(tmp_path: Path) -> None:
    rpc = TopicLimitRpc()
    collector = _collector(tmp_path, rpc)

    logs, blocks = collector._fetch_consistent_chunk(
        address="0x" + "22" * 20,
        start=100,
        end=100,
        topic0s=("0x1", "0x2", "0x3", "0x4"),
    )

    assert [log["topics"][0] for log in logs] == ["0x1", "0x2", "0x3", "0x4"]
    assert tuple(blocks) == (100,)
    assert ("0x1",) in rpc.calls
    assert ("0x4",) in rpc.calls


def test_public_collector_fails_closed_when_one_topic_is_still_over_limit(
    tmp_path: Path,
) -> None:
    rpc = SingleTopicLimitRpc()
    collector = _collector(tmp_path, rpc)

    with pytest.raises(RpcError, match="limit exceeded"):
        collector._fetch_consistent_chunk(
            address="0x" + "22" * 20,
            start=100,
            end=100,
            topic0s=("0x1", "0x2"),
        )


def test_public_collector_rejects_partition_block_hash_disagreement(tmp_path: Path) -> None:
    rpc = PartitionForkRpc()
    collector = _collector(tmp_path, rpc)

    with pytest.raises(RpcError, match="block mismatch"):
        collector._fetch_consistent_chunk(
            address="0x" + "22" * 20,
            start=100,
            end=100,
            topic0s=("0x1", "0x2"),
        )
