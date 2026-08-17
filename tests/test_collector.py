from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.collector import HistoricalCollector
from pancake_prediction.contracts import MARKETS
from pancake_prediction.rpc import RpcError
from pancake_prediction.store import EventStore


class _RangeLimitedRpc:
    def __init__(self, *, max_span: int = 4, fail_non_range: bool = False) -> None:
        self.max_span = max_span
        self.fail_non_range = fail_non_range
        self.calls: list[tuple[int, int]] = []

    def chain_id(self) -> int:
        return 56

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        del address, topic0s
        self.calls.append((from_block, to_block))
        if self.fail_non_range:
            raise RpcError("429 rate limit exceeded")
        if to_block - from_block + 1 > self.max_span:
            raise RpcError("eth_getLogs: block range exceeds provider maximum")
        return []

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "parentHash": "0x" + f"{max(0, number - 1):064x}",
            "timestamp": hex(1_000 + number),
        }


class _ReorgDuringChunkRpc(_RangeLimitedRpc):
    def __init__(self) -> None:
        super().__init__(max_span=10)
        self.log_reads = 0

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        del address, topic0s
        self.calls.append((from_block, to_block))
        self.log_reads += 1
        suffix = "11" if self.log_reads == 1 else "22"
        return [
            {
                "blockNumber": "0x5",
                "blockHash": "0x" + suffix * 32,
                "transactionHash": "0x" + "44" * 32,
                "transactionIndex": "0x0",
                "logIndex": "0x0",
                "topics": ["0x" + "55" * 32],
                "data": "0x",
            }
        ]

    def block(self, number: int) -> dict[str, Any]:
        assert number == 5
        return {
            "number": "0x5",
            "hash": "0x" + "22" * 32,
            "parentHash": "0x" + "aa" * 32,
            "timestamp": "0x3ed",
        }


def test_collector_adapts_only_to_log_range_limits(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    rpc = _RangeLimitedRpc(max_span=4)
    collector = HistoricalCollector(rpc, store, chunk_size=16)
    report = collector.collect_market(
        MARKETS["BNBUSD"],
        1,
        12,
        include_chainlink=False,
        prediction_analytic_only=True,
    )
    assert report["prediction_events_inserted"] == 0
    assert rpc.calls[:3] == [(1, 12), (1, 8), (1, 4)]
    assert [(a, b) for a, b in rpc.calls if b - a + 1 <= 4] == [(1, 4), (5, 8), (9, 12)]
    run = store.collector_run(int(report["collector_run_id"]))
    assert run is not None and run["status"] == "success"


def test_collector_does_not_hide_rate_limit_errors(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    rpc = _RangeLimitedRpc(fail_non_range=True)
    collector = HistoricalCollector(rpc, store, chunk_size=16)
    with pytest.raises(RpcError, match="rate limit"):
        collector.collect_market(
            MARKETS["BNBUSD"],
            1,
            12,
            include_chainlink=False,
            prediction_analytic_only=True,
        )
    assert rpc.calls == [(1, 12)]


def test_collector_retries_chunk_if_log_block_hash_is_not_canonical(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    rpc = _ReorgDuringChunkRpc()
    collector = HistoricalCollector(rpc, store, chunk_size=10, consistency_retries=3)
    report = collector.collect_market(
        MARKETS["BNBUSD"],
        5,
        5,
        include_chainlink=False,
    )
    assert rpc.log_reads == 2
    assert report["prediction_events_inserted"] == 1
    with store.connect() as conn:
        row = conn.execute("SELECT block_hash FROM events").fetchone()
    assert row is not None
    assert row["block_hash"] == "0x" + "22" * 32
