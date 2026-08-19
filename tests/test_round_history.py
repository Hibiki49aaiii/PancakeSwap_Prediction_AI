from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.pancake_events import LifecycleKind, RoundLifecycleObservation
from pancake_prediction_ai.round_history import (
    backfill_round_lifecycle_logs,
    build_round_timelines,
)


def _event(kind: LifecycleKind, epoch: int, time_ns: int, observed_ns: int, price=None) -> EventRecord:
    return EventRecord(
        event_id=f"{epoch}:{kind.value}:{time_ns}",
        source="pancake_prediction",
        topic="prediction.round_lifecycle",
        event_time_ns=time_ns,
        observed_at_ns=observed_ns,
        payload={
            "kind": kind.value,
            "epoch": epoch,
            "price": price,
        },
    )


def test_completed_timelines_preserve_three_protocol_outcomes() -> None:
    events = []
    for epoch, lock_price, close_price in (
        (1, 100, 101),
        (2, 100, 99),
        (3, 100, 100),
    ):
        base = epoch * 1_000
        events.extend(
            [
                _event(LifecycleKind.START, epoch, base, base + 10),
                _event(LifecycleKind.LOCK, epoch, base + 100, base + 110, lock_price),
                _event(LifecycleKind.END, epoch, base + 200, base + 210, close_price),
            ]
        )
    result = build_round_timelines(events)
    assert result.incomplete_epochs == ()
    assert [item.outcome for item in result.completed] == [Outcome.BULL, Outcome.BEAR, Outcome.TIE]
    assert result.completed[0].label_available_at_ns == 1_210
    assert result.completed[0].lock_available_at_ns == 1_110


def test_partial_range_surfaces_incomplete_epoch_instead_of_fabricating_label() -> None:
    events = [
        _event(LifecycleKind.START, 7, 1_000, 1_010),
        _event(LifecycleKind.LOCK, 7, 1_100, 1_110, 100),
    ]
    result = build_round_timelines(events)
    assert result.completed == ()
    assert result.incomplete_epochs == (7,)


def test_duplicate_lifecycle_event_is_hard_failure() -> None:
    start = _event(LifecycleKind.START, 7, 1_000, 1_010)
    with pytest.raises(ValueError, match="duplicate START"):
        build_round_timelines([start, replace(start, event_id="other")])


def test_lifecycle_backfill_reconstructs_block_availability_and_chunks_atomically(tmp_path, monkeypatch) -> None:
    calls = []

    def fake_collect(client, *, from_block, to_block, prediction_contract):
        calls.append((from_block, to_block))
        if from_block == 10:
            event = EventRecord(
                event_id="raw-start",
                source="pancake_prediction",
                topic="prediction.round_lifecycle",
                event_time_ns=1_000_000_000_000,
                observed_at_ns=9_000_000_000_000,
                payload={"kind": "START", "epoch": 1, "price": None},
            )
            return (
                RoundLifecycleObservation(
                    kind=LifecycleKind.START,
                    epoch=1,
                    block_number=10,
                    block_hash="0x" + "ab" * 32,
                    block_timestamp_s=1000,
                    transaction_hash="0x" + "cd" * 32,
                    log_index=0,
                    oracle_round_id=None,
                    price=None,
                    event=event,
                ),
            )
        return ()

    monkeypatch.setattr(
        "pancake_prediction_ai.round_history.collect_round_lifecycle_logs",
        fake_collect,
    )
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        result = backfill_round_lifecycle_logs(
            object(),  # type: ignore[arg-type]
            store,
            dataset_id="round-history-v1",
            from_block=10,
            to_block=14,
            assumed_latency_ns=2_000_000_000,
            chunk_size=2,
        )
        assert result.chunks_completed == 3
        assert result.events_appended == 1
        loaded = store.read_all_ingest_order()[0].event
        assert loaded.observed_at_ns == 1_002_000_000_000
        metadata = loaded.payload["_availability_provenance"]
        assert metadata["availability_basis"] == "block_timestamp"
        assert metadata["captured_at_ns"] == 9_000_000_000_000
        assert store.verify_chain()

    assert calls == [(10, 11), (12, 13), (14, 14)]
