from __future__ import annotations

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.shadow_economics import (
    ShadowEconomicAction,
    ShadowEconomicPolicy,
    record_shadow_economic_decision,
)
from pancake_prediction_ai.shadow_inference import ShadowInferenceResult


def _round_event(event_id: str, *, bull: int, bear: int, observed_at_ns: int) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        source="pancake_prediction",
        topic="prediction.round_snapshot",
        event_time_ns=observed_at_ns,
        observed_at_ns=observed_at_ns,
        payload={
            "epoch": 7,
            "bull_amount_wei": bull,
            "bear_amount_wei": bear,
            "total_amount_wei": bull + bear,
            "treasury_fee_ppm": 20_000,
        },
    )


def _inference(store: EventStore, *, probability_bull: float, probability_tie: float):
    source = store.read_all_ingest_order()[-1]
    decision = store.append(
        EventRecord(
            event_id="shadow:model_decision:7",
            source="shadow",
            topic="shadow.model_decision",
            event_time_ns=20,
            observed_at_ns=20,
            payload={"round_id": 7},
        )
    )
    return ShadowInferenceResult(
        accepted=True,
        blockers=(),
        round_id=7,
        probability_bull=probability_bull,
        probability_bear=1.0 - probability_bull - probability_tie,
        probability_tie=probability_tie,
        predicted_outcome="BULL",
        promoted_model_artifact_sha256="a" * 64,
        source_snapshot_tip_hash=source.event_hash,
        source_snapshot_digest="b" * 64,
        source_snapshot_event_count=1,
        stored_decision=decision,
    )


def test_shadow_economic_decision_uses_exact_pool_at_model_source_tip(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append(_round_event("round:initial", bull=100, bear=300, observed_at_ns=10))
        inference = _inference(store, probability_bull=0.80, probability_tie=0.001)

        # A later observation must not alter the pool used by the economic decision.
        store.append(_round_event("round:later", bull=9_000, bear=1_000, observed_at_ns=25))
        result = record_shadow_economic_decision(
            inference,
            store,
            policy=ShadowEconomicPolicy(stake_wei=10),
            clock_ns=lambda: 30,
        )

        assert result.action is ShadowEconomicAction.BET
        assert result.selected_side is not None
        assert result.selected_side.value == "BULL"
        payload = result.stored_event.event.payload
        assert payload["pool"] == {
            "bull_wei": 100,
            "bear_wei": 300,
            "treasury_fee_ppm": 20_000,
        }
        assert payload["source_round_snapshot_event_id"] == "round:initial"
        assert payload["source_snapshot_tip_hash"] == inference.source_snapshot_tip_hash
        assert store.verify_chain()


def test_shadow_economic_decision_records_abstain_when_both_sides_are_negative_ev(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append(_round_event("round:balanced", bull=100, bear=100, observed_at_ns=10))
        inference = _inference(store, probability_bull=0.50, probability_tie=0.001)
        result = record_shadow_economic_decision(
            inference,
            store,
            policy=ShadowEconomicPolicy(stake_wei=10),
            clock_ns=lambda: 30,
        )
        assert result.action is ShadowEconomicAction.ABSTAIN
        assert result.selected_side is None
        assert result.bull.expected_pnl_wei < 0
        assert result.bear.expected_pnl_wei < 0
        assert result.stored_event.event.payload["selected_side"] is None


def test_shadow_economic_threshold_can_force_abstention_from_small_positive_edge(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        store.append(_round_event("round:threshold", bull=100, bear=300, observed_at_ns=10))
        inference = _inference(store, probability_bull=0.80, probability_tie=0.001)
        result = record_shadow_economic_decision(
            inference,
            store,
            policy=ShadowEconomicPolicy(stake_wei=10, min_expected_return=10.0),
            clock_ns=lambda: 30,
        )
        assert result.action is ShadowEconomicAction.ABSTAIN
        assert result.selected_side is None
