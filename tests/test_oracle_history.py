from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.oracle_history import build_active_oracle_history
from pancake_prediction.store import EventStore

OLD_ORACLE = "0x" + "11" * 20
NEW_ORACLE = "0x" + "22" * 20
MARKET = "BNBUSD"
PREDICTION = "0x" + "33" * 20


def _block(number: int, *, salt: int = 0) -> dict[str, Any]:
    value = number * 100 + salt + 1
    parent = max(0, number - 1) * 100 + salt + 1
    return {
        "number": hex(number),
        "hash": "0x" + f"{value:064x}",
        "parentHash": "0x" + f"{parent:064x}",
        "timestamp": hex(1_700_000_000 + number),
    }


def _insert_event(
    store: EventStore,
    *,
    block: dict[str, Any],
    contract_address: str,
    source: str,
    event_name: str,
    decoded: dict[str, object],
    tx_index: int,
    log_index: int,
) -> None:
    store.insert_event(
        chain_id=56,
        contract_address=contract_address,
        market=MARKET,
        source=source,
        log={
            "blockNumber": block["number"],
            "blockHash": block["hash"],
            "transactionHash": "0x" + f"{int(block['number'], 16) * 1000 + log_index:064x}",
            "transactionIndex": hex(tx_index),
            "logIndex": hex(log_index),
            "topics": ["0x" + "44" * 32],
            "data": "0x",
        },
        event_name=event_name,
        decoded=decoded,
    )


def _new_oracle(
    store: EventStore,
    block: dict[str, Any],
    address: str,
    *,
    tx_index: int,
    log_index: int,
) -> None:
    _insert_event(
        store,
        block=block,
        contract_address=PREDICTION,
        source="prediction",
        event_name="NewOracle",
        decoded={"oracle": address},
        tx_index=tx_index,
        log_index=log_index,
    )


def _answer(
    store: EventStore,
    block: dict[str, Any],
    address: str,
    *,
    price: int,
    tx_index: int,
    log_index: int,
) -> None:
    timestamp = int(str(block["timestamp"]), 16)
    _insert_event(
        store,
        block=block,
        contract_address=address,
        source="chainlink",
        event_name="AnswerUpdated",
        decoded={"current": price, "roundId": log_index + 1, "updatedAt": timestamp},
        tx_index=tx_index,
        log_index=log_index,
    )


def _initialized_store(path: Path, *, anchor_block: int = 10) -> EventStore:
    store = EventStore(path)
    store.initialize()
    store.record_metadata(f"{MARKET}.oracle_anchor_block", str(anchor_block))
    store.record_metadata(f"{MARKET}.oracle_anchor_address", OLD_ORACLE)
    return store


def test_missing_anchor_fails_closed(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    with pytest.raises(ValueError, match="missing oracle anchor metadata"):
        build_active_oracle_history(store.path, MARKET)


def test_anchor_applies_from_next_block_not_earlier_in_same_block(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path / "events.sqlite3", anchor_block=10)
    block10 = _block(10)
    block11 = _block(11)
    store.upsert_block(56, block10)
    store.upsert_block(56, block11)
    _answer(store, block10, OLD_ORACLE, price=100, tx_index=0, log_index=0)
    _answer(store, block11, OLD_ORACLE, price=101, tx_index=0, log_index=0)

    history = build_active_oracle_history(store.path, MARKET)

    assert [event.decoded["current"] for event in history.events] == [101]
    assert history.excluded_unanchored == 1
    assert history.excluded_inactive_oracle == 0


def test_same_block_oracle_switch_uses_exact_evm_order(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path / "events.sqlite3")
    block12 = _block(12)
    store.upsert_block(56, block12)
    _answer(store, block12, OLD_ORACLE, price=100, tx_index=0, log_index=0)
    _new_oracle(store, block12, NEW_ORACLE, tx_index=1, log_index=1)
    _answer(store, block12, OLD_ORACLE, price=999, tx_index=2, log_index=2)
    _answer(store, block12, NEW_ORACLE, price=200, tx_index=3, log_index=3)

    history = build_active_oracle_history(store.path, MARKET)

    assert [event.decoded["current"] for event in history.events] == [100, 200]
    assert history.excluded_inactive_oracle == 1
    assert history.excluded_unanchored == 0
    assert history.activations[-1].address == NEW_ORACLE


def test_explicit_anchor_block_new_oracle_can_establish_same_block_state(
    tmp_path: Path,
) -> None:
    store = _initialized_store(tmp_path / "events.sqlite3", anchor_block=10)
    block10 = _block(10)
    store.upsert_block(56, block10)
    _answer(store, block10, OLD_ORACLE, price=90, tx_index=0, log_index=0)
    _new_oracle(store, block10, OLD_ORACLE, tx_index=1, log_index=1)
    _answer(store, block10, OLD_ORACLE, price=100, tx_index=2, log_index=2)

    history = build_active_oracle_history(store.path, MARKET)

    assert [event.decoded["current"] for event in history.events] == [100]
    assert history.excluded_unanchored == 1


def test_anchor_disagreement_with_anchor_block_event_is_rejected(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path / "events.sqlite3", anchor_block=10)
    block10 = _block(10)
    store.upsert_block(56, block10)
    _new_oracle(store, block10, NEW_ORACLE, tx_index=0, log_index=0)

    with pytest.raises(ValueError, match="anchor disagrees"):
        build_active_oracle_history(store.path, MARKET)


def test_orphaned_chainlink_event_is_excluded_by_canonical_join(tmp_path: Path) -> None:
    store = _initialized_store(tmp_path / "events.sqlite3")
    old_block = _block(12, salt=0)
    store.upsert_block(56, old_block)
    _answer(store, old_block, OLD_ORACLE, price=999, tx_index=0, log_index=0)

    replacement = _block(12, salt=1)
    store.upsert_block(56, replacement)

    history = build_active_oracle_history(store.path, MARKET)

    assert history.events == ()
    assert history.canonical_answer_updates == 0
