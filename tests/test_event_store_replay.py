from __future__ import annotations

import sqlite3

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.replay import build_snapshot, freshness_ns, latest_numeric


def event(event_id: str, *, observed: int, price: float, event_time: int | None = None) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        source="binance",
        topic="bnb_price",
        event_time_ns=observed if event_time is None else event_time,
        observed_at_ns=observed,
        payload={"price": price},
    )


def test_event_store_hash_chain_and_as_of_order(tmp_path) -> None:
    path = tmp_path / "events.sqlite"
    with EventStore(path) as store:
        store.append(event("late-ingest", observed=200, price=2.0))
        store.append(event("early-observation", observed=100, price=1.0))
        store.append(event("future", observed=300, price=3.0))
        assert store.verify_chain()
        snapshot = store.read_as_of(200)
        assert [item.event.event_id for item in snapshot] == ["early-observation", "late-ingest"]


def test_duplicate_event_id_rejected(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(event("same", observed=1, price=1.0))
        with pytest.raises(ValueError, match="duplicate"):
            store.append(event("same", observed=2, price=2.0))


def test_hash_chain_detects_external_payload_mutation(tmp_path) -> None:
    path = tmp_path / "events.sqlite"
    with EventStore(path) as store:
        store.append(event("e1", observed=1, price=1.0))
        store.append(event("e2", observed=2, price=2.0))
        assert store.verify_chain()

    conn = sqlite3.connect(path)
    conn.execute("UPDATE events SET payload_json = ? WHERE event_id = ?", ('{"price":999}', "e1"))
    conn.commit()
    conn.close()

    with EventStore(path) as store:
        assert not store.verify_chain()


def test_replay_excludes_future_observations_even_if_event_time_is_old(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(event("known", observed=100, event_time=90, price=10.0))
        store.append(event("late-arrival", observed=250, event_time=80, price=999.0))
        all_events = store.read_all_ingest_order()

    snapshot = build_snapshot(all_events, cutoff_ns=200)
    assert [item.event.event_id for item in snapshot.events] == ["known"]
    assert latest_numeric(snapshot, source="binance", topic="bnb_price", field="price") == 10.0
    assert freshness_ns(snapshot, source="binance", topic="bnb_price") == 100
