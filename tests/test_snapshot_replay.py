from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pancake_prediction.contracts import MARKETS
from pancake_prediction.events import (
    EventDecodeError,
    PREDICTION_EVENT_TOPICS,
    decode_prediction_log,
)
from pancake_prediction.replay import (
    ReplayInvariantError,
    replay_canonical_snapshot,
    replay_prediction_logs,
)
from pancake_prediction.snapshot import (
    SnapshotLog,
    canonical_logs,
    freeze_canonical_snapshot,
    raw_event_export_hash,
)
from pancake_prediction.store import BlockObservation, DataIntegrityError, EventStore, RawLogObservation


ADDRESS = MARKETS["BNBUSD"].address
SENDER = "0x2222222222222222222222222222222222222222"


def _hash(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _topic_uint(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _topic_address(address: str) -> str:
    raw = bytes.fromhex(address.removeprefix("0x"))
    return "0x" + (b"\x00" * 12 + raw).hex()


def _word_uint(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def _word_int(value: int) -> str:
    return value.to_bytes(32, "big", signed=True).hex()


def _snapshot_log(
    name: str,
    *,
    block: int,
    log_index: int,
    topics: tuple[str, ...],
    data: str,
    removed: bool = False,
) -> SnapshotLog:
    return SnapshotLog(
        block_number=block,
        block_hash=_hash(1000 + block),
        tx_hash=_hash(2000 + block * 10 + log_index),
        tx_index=0,
        log_index=log_index,
        address=ADDRESS,
        topics=(PREDICTION_EVENT_TOPICS[name],) + topics,
        data=data,
        removed=removed,
    )


def _start(epoch: int, block: int, log_index: int = 0) -> SnapshotLog:
    return _snapshot_log(
        "StartRound",
        block=block,
        log_index=log_index,
        topics=(_topic_uint(epoch),),
        data="0x",
    )


def _bet(side: str, epoch: int, amount: int, block: int, log_index: int) -> SnapshotLog:
    return _snapshot_log(
        "BetBull" if side == "bull" else "BetBear",
        block=block,
        log_index=log_index,
        topics=(_topic_address(SENDER), _topic_uint(epoch)),
        data="0x" + _word_uint(amount),
    )


def _lock(epoch: int, oracle_id: int, price: int, block: int, log_index: int = 0) -> SnapshotLog:
    return _snapshot_log(
        "LockRound",
        block=block,
        log_index=log_index,
        topics=(_topic_uint(epoch), _topic_uint(oracle_id)),
        data="0x" + _word_int(price),
    )


def _end(epoch: int, oracle_id: int, price: int, block: int, log_index: int = 0) -> SnapshotLog:
    return _snapshot_log(
        "EndRound",
        block=block,
        log_index=log_index,
        topics=(_topic_uint(epoch), _topic_uint(oracle_id)),
        data="0x" + _word_int(price),
    )


def _rewards(
    epoch: int,
    reward_base: int,
    reward_amount: int,
    treasury: int,
    block: int,
    log_index: int = 1,
) -> SnapshotLog:
    return _snapshot_log(
        "RewardsCalculated",
        block=block,
        log_index=log_index,
        topics=(_topic_uint(epoch),),
        data="0x" + _word_uint(reward_base) + _word_uint(reward_amount) + _word_uint(treasury),
    )


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "events.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_freeze_requires_every_height(self) -> None:
        self.store.ingest_observation_batch(
            blocks=[BlockObservation(56, 10, _hash(10), _hash(9), 100)],
            logs=[],
            observed_at=1,
        )
        with self.assertRaisesRegex(DataIntegrityError, "missing heights"):
            freeze_canonical_snapshot(self.store, chain_id=56, start_block=10, end_block=11)

    def test_snapshot_and_export_hash_are_frozen_across_later_reorg(self) -> None:
        old_blocks = [
            BlockObservation(56, 10, _hash(10), _hash(9), 100),
            BlockObservation(56, 11, _hash(11), _hash(10), 103),
        ]
        old_log = RawLogObservation(
            56,
            11,
            _hash(11),
            _hash(501),
            0,
            0,
            ADDRESS,
            (PREDICTION_EVENT_TOPICS["StartRound"], _topic_uint(7)),
            "0x",
        )
        self.store.ingest_observation_batch(blocks=old_blocks, logs=[old_log], observed_at=1)
        old_snapshot = freeze_canonical_snapshot(self.store, chain_id=56, start_block=10, end_block=11)
        old_logs = canonical_logs(self.store, old_snapshot, address=ADDRESS)
        old_export = raw_event_export_hash(old_snapshot, old_logs)

        new_blocks = [
            BlockObservation(56, 10, _hash(20), _hash(9), 100),
            BlockObservation(56, 11, _hash(21), _hash(20), 103),
        ]
        new_log = RawLogObservation(
            56,
            11,
            _hash(21),
            _hash(601),
            0,
            0,
            ADDRESS,
            (PREDICTION_EVENT_TOPICS["StartRound"], _topic_uint(8)),
            "0x",
        )
        self.store.ingest_observation_batch(blocks=new_blocks, logs=[new_log], observed_at=2)
        new_snapshot = freeze_canonical_snapshot(self.store, chain_id=56, start_block=10, end_block=11)
        new_logs = canonical_logs(self.store, new_snapshot, address=ADDRESS)

        self.assertEqual(canonical_logs(self.store, old_snapshot, address=ADDRESS), old_logs)
        self.assertEqual(raw_event_export_hash(old_snapshot, old_logs), old_export)
        self.assertNotEqual(old_snapshot.snapshot_hash, new_snapshot.snapshot_hash)
        self.assertNotEqual(old_export, raw_event_export_hash(new_snapshot, new_logs))
        self.assertEqual(len(new_logs), 1)
        self.assertEqual(decode_prediction_log(new_logs[0]).epoch, 8)  # type: ignore[union-attr]

    def test_export_hash_rejects_log_from_other_fork(self) -> None:
        blocks = [
            BlockObservation(56, 10, _hash(10), _hash(9), 100),
            BlockObservation(56, 11, _hash(11), _hash(10), 103),
        ]
        self.store.ingest_observation_batch(blocks=blocks, logs=[], observed_at=1)
        snapshot = freeze_canonical_snapshot(self.store, chain_id=56, start_block=10, end_block=11)
        alien = SnapshotLog(
            11, _hash(999), _hash(500), 0, 0, ADDRESS, (_hash(1),), "0x", False
        )
        with self.assertRaisesRegex(DataIntegrityError, "outside the frozen canonical snapshot"):
            raw_event_export_hash(snapshot, (alien,))



class EventDecodeTests(unittest.TestCase):
    def test_bet_bull_decodes_indexed_sender_epoch_and_amount(self) -> None:
        event = decode_prediction_log(_bet("bull", 77, 123456, 10, 0))
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.name, "BetBull")
        self.assertEqual(event.sender, SENDER)
        self.assertEqual(event.epoch, 77)
        self.assertEqual(event.amount_wei, 123456)

    def test_lock_round_decodes_signed_price(self) -> None:
        event = decode_prediction_log(_lock(77, 9001, -123, 11))
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.oracle_round_id, 9001)
        self.assertEqual(event.price, -123)

    def test_removed_known_event_fails_closed(self) -> None:
        with self.assertRaises(EventDecodeError):
            decode_prediction_log(
                _snapshot_log(
                    "StartRound",
                    block=10,
                    log_index=0,
                    topics=(_topic_uint(77),),
                    data="0x",
                    removed=True,
                )
            )

    def test_unknown_event_is_ignored_not_misdecoded(self) -> None:
        log = SnapshotLog(10, _hash(1010), _hash(2000), 0, 0, ADDRESS, (_hash(999),), "0x", False)
        self.assertIsNone(decode_prediction_log(log))


class ReplayTests(unittest.TestCase):
    def test_replay_round_lifecycle_and_outcome(self) -> None:
        epoch = 55
        logs = (
            _start(epoch, 10),
            _bet("bull", epoch, 100, 10, 1),
            _bet("bear", epoch, 50, 10, 2),
            _lock(epoch, 1000, 2000, 11),
            _end(epoch, 1001, 2100, 12),
            _rewards(epoch, 100, 146, 4, 12),
        )
        report = replay_prediction_logs(logs)
        self.assertEqual(report.recognized_events, 6)
        self.assertEqual(report.ignored_logs, 0)
        self.assertEqual(len(report.rounds), 1)
        round_ = report.rounds[0]
        self.assertEqual(round_.outcome, "bull")
        self.assertEqual(round_.total_amount_wei, 150)
        self.assertEqual(round_.bull_amount_wei, 100)
        self.assertEqual(round_.bear_amount_wei, 50)
        self.assertEqual(round_.reward_base_cal_amount, 100)

    def test_bet_after_lock_is_rejected(self) -> None:
        epoch = 55
        logs = (_start(epoch, 10), _lock(epoch, 1000, 2000, 11), _bet("bull", epoch, 100, 12, 0))
        with self.assertRaisesRegex(ReplayInvariantError, "bet after lock"):
            replay_prediction_logs(logs)

    def test_reward_base_mismatch_is_rejected(self) -> None:
        epoch = 55
        logs = (
            _start(epoch, 10),
            _bet("bull", epoch, 100, 10, 1),
            _lock(epoch, 1000, 2000, 11),
            _end(epoch, 1001, 2100, 12),
            _rewards(epoch, 99, 100, 0, 12),
        )
        with self.assertRaisesRegex(ReplayInvariantError, "reward base mismatch"):
            replay_prediction_logs(logs)

    def test_partial_replay_can_disable_missing_start_invariant(self) -> None:
        epoch = 55
        logs = (_bet("bear", epoch, 50, 10, 0), _lock(epoch, 1000, 2000, 11))
        report = replay_prediction_logs(logs, strict_lifecycle=False)
        self.assertEqual(report.rounds[0].bear_amount_wei, 50)
        self.assertEqual(report.rounds[0].lock_price, 2000)

    def test_integrated_replay_artifact_binds_snapshot_and_export_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.sqlite3")
            store.initialize()
            block = BlockObservation(56, 10, _hash(10), _hash(9), 100)
            raw = RawLogObservation(
                56,
                10,
                _hash(10),
                _hash(700),
                0,
                0,
                ADDRESS,
                (PREDICTION_EVENT_TOPICS["StartRound"], _topic_uint(9)),
                "0x",
            )
            store.ingest_observation_batch(blocks=[block], logs=[raw], observed_at=1)
            snapshot = freeze_canonical_snapshot(store, chain_id=56, start_block=10, end_block=10)
            artifact = replay_canonical_snapshot(
                store, snapshot, market_symbol="BNBUSD", strict_lifecycle=True
            )
            self.assertEqual(artifact.snapshot_hash, snapshot.snapshot_hash)
            self.assertEqual(artifact.market, "BNBUSD")
            self.assertEqual(artifact.replay.rounds[0].epoch, 9)
            self.assertEqual(len(artifact.raw_event_export_hash), 66)

    def test_unsorted_logs_are_rejected(self) -> None:
        epoch = 55
        logs = (_lock(epoch, 1000, 2000, 11), _start(epoch, 10))
        with self.assertRaisesRegex(ReplayInvariantError, "deterministic chain order"):
            replay_prediction_logs(logs, strict_lifecycle=False)


if __name__ == "__main__":
    unittest.main()
