from __future__ import annotations

from types import SimpleNamespace

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.rpc_snapshot import BlockAnchor
from pancake_prediction_ai.shadow_settlement_batch import (
    reconcile_pending_shadow_economic_rounds,
    unresolved_shadow_economic_round_ids,
)


def _event(topic: str, round_id: int) -> EventRecord:
    return EventRecord(
        event_id=f"{topic}:{round_id}",
        source="shadow",
        topic=topic,
        event_time_ns=round_id,
        observed_at_ns=round_id,
        payload={"round_id": round_id},
    )


def test_unresolved_scan_subtracts_existing_settlements(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append_many(
            (
                _event("shadow.economic_decision", 1),
                _event("shadow.economic_decision", 2),
                _event("shadow.economic_decision", 3),
                _event("shadow.economic_settlement", 2),
            )
        )
        assert unresolved_shadow_economic_round_ids(store) == (1, 3)


def test_batch_uses_one_pinned_anchor_for_multiple_unresolved_rounds(tmp_path, monkeypatch) -> None:
    attempted: list[tuple[int, int]] = []
    fetches = 0
    anchor = BlockAnchor(100, "0x" + "ab" * 32, 2_000)

    def fake_reconcile(store, client, *, round_id, anchor_fetcher, **kwargs):
        pinned = anchor_fetcher(client)
        attempted.append((round_id, pinned.number))
        return SimpleNamespace(round_id=round_id, status="SETTLED")

    def fetch(client):
        nonlocal fetches
        fetches += 1
        return anchor

    monkeypatch.setattr(
        "pancake_prediction_ai.shadow_settlement_batch.reconcile_shadow_economic_round",
        fake_reconcile,
    )

    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append_many(
            (
                _event("shadow.economic_decision", 4),
                _event("shadow.economic_decision", 5),
            )
        )
        result = reconcile_pending_shadow_economic_rounds(
            store,
            object(),  # type: ignore[arg-type]
            anchor_fetcher=fetch,
        )

    assert fetches == 1
    assert result.anchor == anchor
    assert result.attempted_round_ids == (4, 5)
    assert attempted == [(4, 100), (5, 100)]


def test_batch_max_rounds_limits_oldest_unresolved_first(tmp_path, monkeypatch) -> None:
    attempted: list[int] = []
    anchor = BlockAnchor(100, "0x" + "ab" * 32, 2_000)

    def fake_reconcile(store, client, *, round_id, **kwargs):
        attempted.append(round_id)
        return SimpleNamespace(round_id=round_id, status="SETTLED")

    monkeypatch.setattr(
        "pancake_prediction_ai.shadow_settlement_batch.reconcile_shadow_economic_round",
        fake_reconcile,
    )
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append_many(
            tuple(_event("shadow.economic_decision", round_id) for round_id in (7, 3, 5))
        )
        result = reconcile_pending_shadow_economic_rounds(
            store,
            object(),  # type: ignore[arg-type]
            max_rounds=2,
            anchor_fetcher=lambda client: anchor,
        )

    assert result.attempted_round_ids == (3, 5)
    assert attempted == [3, 5]
