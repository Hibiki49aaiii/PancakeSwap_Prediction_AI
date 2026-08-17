from pathlib import Path
from typing import Any

from pancake_prediction.store import EventStore


def _block(number: int, block_hash: str, parent_hash: str | None = None) -> dict[str, Any]:
    return {
        "number": hex(number),
        "hash": block_hash,
        "parentHash": parent_hash or "0x" + "aa" * 32,
        "timestamp": hex(1_700_000_000 + number),
    }


def test_reorg_marks_old_block_noncanonical(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "db.sqlite3")
    store.initialize()
    old_hash = "0x" + "11" * 32
    new_hash = "0x" + "22" * 32
    assert store.upsert_block(56, _block(100, old_hash)) is False
    assert store.upsert_block(56, _block(100, new_hash)) is True
    canonical = store.canonical_blocks_from(56, 100)
    assert len(canonical) == 1
    assert canonical[0]["hash"] == new_hash


def test_reorg_can_return_to_previously_seen_hash(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "db.sqlite3")
    store.initialize()
    first_hash = "0x" + "11" * 32
    second_hash = "0x" + "22" * 32
    store.upsert_block(56, _block(100, first_hash))
    store.upsert_block(56, _block(100, second_hash))
    assert store.upsert_block(56, _block(100, first_hash)) is True
    canonical = store.canonical_blocks_from(56, 100)
    assert len(canonical) == 1
    assert canonical[0]["hash"] == first_hash


def test_event_insert_is_idempotent(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "db.sqlite3")
    store.initialize()
    block_hash = "0x" + "33" * 32
    store.upsert_block(56, _block(101, block_hash))
    log: dict[str, Any] = {
        "blockNumber": hex(101),
        "blockHash": block_hash,
        "transactionHash": "0x" + "44" * 32,
        "transactionIndex": "0x0",
        "logIndex": "0x1",
        "topics": ["0x" + "55" * 32],
        "data": "0x",
    }
    assert store.insert_event(
        chain_id=56,
        contract_address="0x" + "66" * 20,
        market="BNBUSD",
        source="prediction",
        log=log,
        event_name=None,
        decoded=None,
    ) is True
    assert store.insert_event(
        chain_id=56,
        contract_address="0x" + "66" * 20,
        market="BNBUSD",
        source="prediction",
        log=log,
        event_name=None,
        decoded=None,
    ) is False


def test_foreign_keys_enabled_on_every_connection(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "db.sqlite3")
    store.initialize()
    with store.connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_collector_run_is_append_only_lifecycle(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "db.sqlite3")
    store.initialize()
    run_id = store.begin_collector_run(
        chain_id=56,
        market="BNBUSD",
        contract_address="0x" + "11" * 20,
        from_block=1,
        to_block=10,
        details={"chunk_size": 4},
    )
    store.finish_collector_run(run_id, status="success", details={"inserted": 3})
    row = store.collector_run(run_id)
    assert row is not None
    assert row["status"] == "success"
