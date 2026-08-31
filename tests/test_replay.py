from pathlib import Path

from pancake_prediction.replay import ChainEvent, build_replay_snapshot, replay_events
from pancake_prediction.store import EventStore


def _event(
    *, block: int, timestamp: int, log_index: int, name: str, decoded: dict[str, object]
) -> ChainEvent:
    return ChainEvent(
        block_number=block,
        block_hash="0x" + f"{block:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{block * 100 + log_index:064x}",
        tx_index=0,
        log_index=log_index,
        event_name=name,
        decoded=decoded,
    )


def test_replay_is_byte_deterministic_and_order_stable() -> None:
    events = [
        _event(
            block=11,
            timestamp=1300,
            log_index=1,
            name="LockRound",
            decoded={"epoch": 7, "roundId": 90, "price": 101},
        ),
        _event(block=10, timestamp=1000, log_index=0, name="StartRound", decoded={"epoch": 7}),
        _event(
            block=10,
            timestamp=1100,
            log_index=2,
            name="BetBull",
            decoded={"epoch": 7, "amount": 30},
        ),
        _event(
            block=10,
            timestamp=1100,
            log_index=1,
            name="BetBear",
            decoded={"epoch": 7, "amount": 20},
        ),
        _event(
            block=12,
            timestamp=1600,
            log_index=0,
            name="EndRound",
            decoded={"epoch": 7, "roundId": 91, "price": 103},
        ),
        _event(
            block=12,
            timestamp=1600,
            log_index=1,
            name="RewardsCalculated",
            decoded={
                "epoch": 7,
                "rewardBaseCalAmount": 30,
                "rewardAmount": 48,
                "treasuryAmount": 2,
            },
        ),
    ]
    first = replay_events("BNBUSD", events)
    second = replay_events("BNBUSD", reversed(events))
    assert first.to_bytes() == second.to_bytes()
    assert first.output_digest == second.output_digest
    record = first.rounds[0]
    assert record.label == "bull"
    assert record.total_amount_wei == 50


def test_replay_flags_temporal_inconsistency() -> None:
    snapshot = replay_events(
        "BNBUSD",
        [
            _event(block=1, timestamp=200, log_index=0, name="StartRound", decoded={"epoch": 1}),
            _event(
                block=2,
                timestamp=100,
                log_index=0,
                name="LockRound",
                decoded={"epoch": 1, "roundId": 1, "price": 10},
            ),
        ],
    )
    assert "lock_before_start" in snapshot.rounds[0].issues


def test_replay_from_store_excludes_orphaned_reorg_event(tmp_path: Path) -> None:
    db = tmp_path / "replay.sqlite3"
    store = EventStore(db)
    store.initialize()
    old_hash = "0x" + "11" * 32
    new_hash = "0x" + "22" * 32
    store.upsert_block(
        56,
        {
            "number": "0xc8",
            "hash": old_hash,
            "parentHash": "0x" + "aa" * 32,
            "timestamp": "0x7d0",
        },
    )

    def insert(block_hash: str, tx_byte: str, epoch: int) -> None:
        store.insert_event(
            chain_id=56,
            contract_address="0x" + "66" * 20,
            market="BNBUSD",
            source="prediction",
            log={
                "blockNumber": "0xc8",
                "blockHash": block_hash,
                "transactionHash": "0x" + tx_byte * 32,
                "transactionIndex": "0x0",
                "logIndex": "0x0",
                "topics": ["0x" + "55" * 32],
                "data": "0x",
            },
            event_name="StartRound",
            decoded={"epoch": epoch},
        )

    insert(old_hash, "44", 1)
    store.upsert_block(
        56,
        {
            "number": "0xc8",
            "hash": new_hash,
            "parentHash": "0x" + "bb" * 32,
            "timestamp": "0x7d1",
        },
    )
    insert(new_hash, "77", 2)
    snapshot = build_replay_snapshot(db, "BNBUSD")
    assert [record.epoch for record in snapshot.rounds] == [2]
