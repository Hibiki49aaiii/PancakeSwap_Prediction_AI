from __future__ import annotations

from pathlib import Path
from typing import Any

from pancake_prediction.contracts import MARKETS
from pancake_prediction.oracle_history import build_active_oracle_history
from pancake_prediction.recent_bootstrap import _record_recent_chainlink_anchor
from pancake_prediction.store import EventStore

ORACLE_PROXY = "0x" + "33" * 20
CHAINLINK_AGGREGATOR = "0x" + "55" * 20


def _block(number: int) -> dict[str, Any]:
    return {
        "number": hex(number),
        "hash": "0x" + f"{number + 1:064x}",
        "parentHash": "0x" + f"{number:064x}",
        "timestamp": hex(1_780_000_000 + number),
    }


def _answer(
    store: EventStore,
    block: dict[str, Any],
    address: str,
    *,
    price: int,
    log_index: int,
) -> None:
    timestamp = int(str(block["timestamp"]), 16)
    store.insert_event(
        chain_id=56,
        contract_address=address,
        market="BNBUSD",
        source="chainlink",
        log={
            "blockNumber": block["number"],
            "blockHash": block["hash"],
            "transactionHash": "0x" + f"{log_index + 1:064x}",
            "transactionIndex": hex(log_index),
            "logIndex": hex(log_index),
            "topics": ["0x" + "44" * 32],
            "data": "0x",
        },
        event_name="AnswerUpdated",
        decoded={"current": price, "roundId": log_index + 1, "updatedAt": timestamp},
    )


def test_recent_anchor_uses_underlying_aggregator_as_active_event_emitter(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    _record_recent_chainlink_anchor(
        store,
        MARKETS["BNBUSD"],
        from_block=100,
        oracle_proxy=ORACLE_PROXY,
        chainlink_aggregator=CHAINLINK_AGGREGATOR,
    )

    block = _block(100)
    store.upsert_block(56, block)
    _answer(store, block, CHAINLINK_AGGREGATOR, price=600 * 10**8, log_index=0)
    _answer(store, block, ORACLE_PROXY, price=999 * 10**8, log_index=1)

    assert store.metadata("BNBUSD.oracle_anchor_block") == "99"
    assert store.metadata("BNBUSD.oracle_anchor_address") == CHAINLINK_AGGREGATOR
    assert store.metadata("BNBUSD.oracle_proxy_anchor_address") == ORACLE_PROXY

    history = build_active_oracle_history(store.path, "BNBUSD")

    assert history.anchor.block_number == 99
    assert history.anchor.address == CHAINLINK_AGGREGATOR
    assert [event.decoded["current"] for event in history.events] == [600 * 10**8]
    assert history.excluded_inactive_oracle == 1
    assert history.excluded_unanchored == 0
