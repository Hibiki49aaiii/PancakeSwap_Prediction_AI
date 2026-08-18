from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from pancake_prediction.collector import (
    CollectionError,
    HistoricalCollector,
    ReorgBeyondLookbackError,
    SnapshotChangedError,
)
from pancake_prediction.contracts import MARKETS
from pancake_prediction.store import (
    BlockObservation,
    DataIntegrityError,
    EventStore,
    RawLogObservation,
)


def _hash(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _address() -> str:
    return MARKETS["BNBUSD"].address


def _block(number: int, block_hash: int, parent_hash: int, timestamp: int | None = None) -> dict[str, Any]:
    return {
        "number": hex(number),
        "hash": _hash(block_hash),
        "parentHash": _hash(parent_hash),
        "timestamp": hex(number * 3 if timestamp is None else timestamp),
    }


def _log(number: int, block_hash: int, tx_hash: int, log_index: int = 0) -> dict[str, Any]:
    return {
        "address": _address(),
        "blockNumber": hex(number),
        "blockHash": _hash(block_hash),
        "transactionHash": _hash(tx_hash),
        "transactionIndex": "0x0",
        "logIndex": hex(log_index),
        "topics": [_hash(999)],
        "data": "0x" + (123).to_bytes(32, "big").hex(),
        "removed": False,
    }


class FakeRpc:
    def __init__(
        self,
        blocks: dict[int, dict[str, Any]],
        logs: list[dict[str, Any]],
        head: int,
        *,
        chain_id: int = 56,
    ) -> None:
        self.blocks = blocks
        self.logs = logs
        self.head = head
        self._chain_id = chain_id
        self.block_reads: dict[int, int] = {}

    def chain_id(self) -> int:
        return self._chain_id

    def block_number(self) -> int:
        return self.head

    def block(self, number: int) -> dict[str, Any]:
        self.block_reads[number] = self.block_reads.get(number, 0) + 1
        return dict(self.blocks[number])

    def get_logs(self, *, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
        assert address.lower() == _address().lower()
        return [
            dict(log)
            for log in self.logs
            if from_block <= int(str(log["blockNumber"]), 16) <= to_block
        ]


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "events.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reorg_preserves_old_block_and_changes_canonical_pointer(self) -> None:
        first = BlockObservation(56, 100, _hash(1000), _hash(999), 1000)
        second = BlockObservation(56, 100, _hash(2000), _hash(1999), 1001)
        self.store.ingest_observation_batch(blocks=[first], logs=[], observed_at=10)
        report = self.store.ingest_observation_batch(blocks=[second], logs=[], observed_at=20)
        self.assertEqual(self.store.block_count(), 2)
        self.assertEqual(self.store.canonical_hash(56, 100), _hash(2000))
        self.assertEqual(self.store.reorg_count(), 1)
        self.assertEqual(report.reorgs_observed, 1)

    def test_conflicting_block_evidence_is_rejected(self) -> None:
        block = BlockObservation(56, 100, _hash(1000), _hash(999), 1000)
        self.store.ingest_observation_batch(blocks=[block], logs=[], observed_at=10)
        conflict = BlockObservation(56, 101, _hash(1000), _hash(999), 1000)
        with self.assertRaises(DataIntegrityError):
            self.store.ingest_observation_batch(blocks=[conflict], logs=[], observed_at=11)

    def test_log_conflict_is_rejected_without_overwrite(self) -> None:
        block = BlockObservation(56, 100, _hash(1000), _hash(999), 1000)
        original = RawLogObservation(
            56, 100, _hash(1000), _hash(55), 0, 0, _address(), (_hash(1),), "0x01"
        )
        self.store.ingest_observation_batch(blocks=[block], logs=[original], observed_at=10)
        changed = RawLogObservation(
            56, 100, _hash(1000), _hash(55), 0, 0, _address(), (_hash(1),), "0x02"
        )
        with self.assertRaises(DataIntegrityError):
            self.store.ingest_observation_batch(blocks=[block], logs=[changed], observed_at=11)
        self.assertEqual(self.store.log_count(), 1)


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "events.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_collects_contiguous_snapshot_and_checkpoint(self) -> None:
        blocks = {
            10: _block(10, 110, 109),
            11: _block(11, 111, 110),
            12: _block(12, 112, 111),
        }
        rpc = FakeRpc(blocks, [_log(11, 111, 500)], head=12)
        report = HistoricalCollector(rpc, self.store).collect_range(
            "BNBUSD", start_block=10, end_block=12, chunk_size=3, observed_at=100, source_key="bnb"
        )
        self.assertEqual(report.blocks_inserted, 3)
        self.assertEqual(report.logs_inserted, 1)
        checkpoint = self.store.checkpoint("bnb")
        self.assertIsNotNone(checkpoint)
        assert checkpoint is not None
        self.assertEqual(checkpoint.last_block, 12)
        self.assertEqual(checkpoint.last_block_hash, _hash(112))
        self.assertEqual(rpc.block_reads[12], 2)

    def test_log_hash_mismatch_aborts_before_evidence_commit(self) -> None:
        blocks = {10: _block(10, 110, 109)}
        rpc = FakeRpc(blocks, [_log(10, 9999, 500)], head=10)
        with self.assertRaises(SnapshotChangedError):
            HistoricalCollector(rpc, self.store).collect_range(
                "BNBUSD", start_block=10, end_block=10, observed_at=100
            )
        self.assertEqual(self.store.block_count(), 0)

    def test_parent_discontinuity_is_rejected(self) -> None:
        blocks = {10: _block(10, 110, 109), 11: _block(11, 111, 999)}
        rpc = FakeRpc(blocks, [], head=11)
        with self.assertRaises(SnapshotChangedError):
            HistoricalCollector(rpc, self.store).collect_range(
                "BNBUSD", start_block=10, end_block=11, observed_at=100
            )

    def test_incremental_plan_rejects_wrong_chain_before_planning(self) -> None:
        rpc = FakeRpc({}, [], head=1050, chain_id=1)
        with self.assertRaisesRegex(CollectionError, "expected BNB Chain id 56"):
            HistoricalCollector(rpc, self.store).plan_incremental(
                "BNBUSD",
                source_key="bnb",
                initial_start_block=500,
            )

    def test_incremental_plan_overlaps_checkpoint_for_reorg_detection(self) -> None:
        self.store.set_checkpoint(
            source_key="bnb",
            chain_id=56,
            market="BNBUSD",
            last_block=1000,
            last_block_hash=_hash(1000),
            updated_at=1,
        )
        rpc = FakeRpc({}, [], head=1050)
        plan = HistoricalCollector(rpc, self.store).plan_incremental(
            "BNBUSD",
            source_key="bnb",
            initial_start_block=500,
            safe_depth=10,
            reorg_lookback_blocks=64,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.start_block, 937)
        self.assertEqual(plan.end_block, 1040)

    def test_reorg_older_than_overlap_boundary_is_explicit_failure(self) -> None:
        old = BlockObservation(56, 9, _hash(109), _hash(108), 9)
        self.store.ingest_observation_batch(blocks=[old], logs=[], observed_at=1)
        blocks = {10: _block(10, 210, 209)}
        rpc = FakeRpc(blocks, [], head=10)
        with self.assertRaises(ReorgBeyondLookbackError):
            HistoricalCollector(rpc, self.store).collect_range(
                "BNBUSD", start_block=10, end_block=10, observed_at=2
            )

    def test_reorg_inside_overlap_preserves_both_forks(self) -> None:
        old_blocks = {
            10: _block(10, 110, 109),
            11: _block(11, 111, 110),
        }
        old_rpc = FakeRpc(old_blocks, [_log(11, 111, 500)], head=11)
        collector = HistoricalCollector(old_rpc, self.store)
        collector.collect_range("BNBUSD", start_block=10, end_block=11, observed_at=10)

        new_blocks = {
            10: _block(10, 210, 109),
            11: _block(11, 211, 210),
        }
        new_rpc = FakeRpc(new_blocks, [_log(11, 211, 600)], head=11)
        HistoricalCollector(new_rpc, self.store).collect_range(
            "BNBUSD", start_block=10, end_block=11, observed_at=20
        )
        self.assertEqual(self.store.block_count(), 4)
        self.assertEqual(self.store.log_count(), 2)
        self.assertEqual(self.store.reorg_count(), 2)
        self.assertEqual(self.store.canonical_hash(56, 11), _hash(211))


if __name__ == "__main__":
    unittest.main()
