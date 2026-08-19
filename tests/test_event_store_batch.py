from __future__ import annotations

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore


def _event(event_id: str, observed_at_ns: int) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        source="test",
        topic="batch",
        event_time_ns=observed_at_ns,
        observed_at_ns=observed_at_ns,
        payload={"id": event_id},
    )


def test_append_many_extends_hash_chain_inside_one_transaction(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        stored = store.append_many((_event("a", 1), _event("b", 2), _event("c", 3)))
        assert len(stored) == 3
        assert stored[0].prev_hash == "GENESIS"
        assert stored[1].prev_hash == stored[0].event_hash
        assert stored[2].prev_hash == stored[1].event_hash
        assert store.verify_chain()
        assert [item.event.event_id for item in store.read_all_ingest_order()] == ["a", "b", "c"]


def test_append_many_rolls_back_entire_batch_when_later_event_conflicts(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        store.append(_event("existing", 1))
        before = store.read_all_ingest_order()
        with pytest.raises(ValueError, match="duplicate or conflicting"):
            store.append_many((_event("would-have-been-first", 2), _event("existing", 3)))
        after = store.read_all_ingest_order()
        assert after == before
        assert store.verify_chain()


def test_duplicate_ids_inside_batch_fail_before_database_mutation(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        with pytest.raises(ValueError, match="inside append batch"):
            store.append_many((_event("dup", 1), _event("dup", 2)))
        assert store.read_all_ingest_order() == ()


def test_empty_batch_is_noop(tmp_path) -> None:
    with EventStore(tmp_path / "events.sqlite") as store:
        assert store.append_many(()) == ()
        assert store.verify_chain()
