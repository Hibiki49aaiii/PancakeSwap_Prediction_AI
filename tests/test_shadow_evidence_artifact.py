from __future__ import annotations

import json

import pytest

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.shadow_evidence_artifact import (
    build_shadow_economic_evidence_artifact,
    load_shadow_economic_evidence_artifact,
)


def _populate(store: EventStore, *, settled: bool = True) -> None:
    round_snapshot = store.append(
        EventRecord(
            event_id="round:7",
            source="pancake_prediction",
            topic="prediction.round_snapshot",
            event_time_ns=10,
            observed_at_ns=10,
            payload={"epoch": 7},
        )
    )
    model = store.append(
        EventRecord(
            event_id="shadow:model_decision:7",
            source="shadow",
            topic="shadow.model_decision",
            event_time_ns=20,
            observed_at_ns=20,
            payload={
                "round_id": 7,
                "source_snapshot_tip_hash": round_snapshot.event_hash,
            },
        )
    )
    economic = store.append(
        EventRecord(
            event_id="shadow:economic_decision:7",
            source="shadow",
            topic="shadow.economic_decision",
            event_time_ns=30,
            observed_at_ns=30,
            payload={
                "round_id": 7,
                "action": "BET",
                "selected_side": "BULL",
                "stake_wei": 100,
                "model_decision_event_id": model.event.event_id,
                "promoted_model_artifact_sha256": "a" * 64,
                "source_snapshot_tip_hash": round_snapshot.event_hash,
                "source_round_snapshot_event_id": round_snapshot.event.event_id,
                "assumed_execution": {
                    "gas_cost_wei": 2,
                    "same_side_inflow_wei": 3,
                    "opposite_side_inflow_wei": 4,
                    "execution_success_probability": 0.75,
                    "min_expected_return": 0.01,
                },
                "bull_ev": {"expected_return_on_stake": 0.2},
                "bear_ev": {"expected_return_on_stake": -0.2},
            },
        )
    )
    if not settled:
        return
    source = store.append(
        EventRecord(
            event_id="pancake:settlement:7",
            source="pancake_prediction",
            topic="prediction.settlement_snapshot",
            event_time_ns=40,
            observed_at_ns=40,
            payload={"epoch": 7, "block_number": 100},
        )
    )
    store.append(
        EventRecord(
            event_id="shadow:economic_settlement:7",
            source="shadow",
            topic="shadow.economic_settlement",
            event_time_ns=40,
            observed_at_ns=40,
            payload={
                "round_id": 7,
                "resolution": "BULL",
                "action": "BET",
                "selected_side": "BULL",
                "pnl_if_executed_wei": 50,
                "probability_adjusted_pnl_wei": 37.5,
                "economic_decision_event_id": economic.event.event_id,
                "settlement_snapshot_event_id": source.event.event_id,
                "block_number": 100,
                "block_hash": "0x" + "ab" * 32,
                "claim_or_refund_gas_modeled": False,
            },
        )
    )


def test_shadow_evidence_artifact_binds_observed_lineage_and_preserves_hybrid_class(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        artifact = build_shadow_economic_evidence_artifact(
            store,
            generated_at_ns=1_000,
        )
        assert artifact.payload["source_event_store"]["tip_hash"] == store.read_all_ingest_order()[-1].event_hash
        assert artifact.payload["summary"]["settled_rounds"] == 1
        assert artifact.payload["completeness"]["all_decisions_settled"] is True
        assert artifact.payload["evidence_classification"]["artifact_class"] == "hybrid_shadow_not_live"
        assert artifact.payload["evidence_classification"]["settlement_outcome_and_final_pool"] == "observed_onchain_when_settled"
        assert artifact.payload["evidence_classification"]["paper_execution"] == "simulated_not_broadcast"
        assert artifact.payload["claims"]["may_support_funded_live_profitability_claim"] is False
        row = artifact.payload["rounds"][0]
        assert row["model_source_snapshot_tip_hash"] == store.read_all_ingest_order()[0].event_hash
        assert row["settlement"]["snapshot_event_hash"] == store.read_all_ingest_order()[-2].event_hash
        assert row["settlement"]["settlement_event_hash"] == store.read_all_ingest_order()[-1].event_hash
        artifact.validate()


def test_shadow_evidence_can_freeze_unresolved_round_without_claiming_completion(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store, settled=False)
        artifact = build_shadow_economic_evidence_artifact(store, generated_at_ns=2_000)
        assert artifact.payload["summary"]["unresolved_rounds"] == 1
        assert artifact.payload["completeness"]["all_decisions_settled"] is False
        assert artifact.payload["completeness"]["has_any_settled_round"] is False
        assert artifact.payload["claims"]["may_support_shadow_model_evaluation"] is False
        assert artifact.payload["rounds"][0]["settlement"] is None


def test_shadow_evidence_rejects_reconstructed_store(tmp_path) -> None:
    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        with pytest.raises(ValueError, match="observed Event Store"):
            build_shadow_economic_evidence_artifact(store, generated_at_ns=1)


def test_shadow_evidence_load_detects_tampering(tmp_path) -> None:
    path = tmp_path / "shadow-evidence.json"
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        artifact = build_shadow_economic_evidence_artifact(store, generated_at_ns=3_000)
        artifact.write(path)
    document = json.loads(path.read_text())
    document["payload"]["claims"]["may_support_funded_live_profitability_claim"] = True
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_shadow_economic_evidence_artifact(path)


def test_shadow_evidence_rejects_settlement_not_directly_chained_to_source_snapshot(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store, settled=False)
        source = store.append(
            EventRecord(
                event_id="pancake:settlement:7",
                source="pancake_prediction",
                topic="prediction.settlement_snapshot",
                event_time_ns=40,
                observed_at_ns=40,
                payload={"epoch": 7},
            )
        )
        store.append(
            EventRecord(
                event_id="intervening",
                source="test",
                topic="test.event",
                event_time_ns=41,
                observed_at_ns=41,
                payload={},
            )
        )
        store.append(
            EventRecord(
                event_id="shadow:economic_settlement:7",
                source="shadow",
                topic="shadow.economic_settlement",
                event_time_ns=42,
                observed_at_ns=42,
                payload={
                    "round_id": 7,
                    "resolution": "BULL",
                    "action": "BET",
                    "selected_side": "BULL",
                    "pnl_if_executed_wei": 10,
                    "probability_adjusted_pnl_wei": 7.5,
                    "economic_decision_event_id": "shadow:economic_decision:7",
                    "settlement_snapshot_event_id": source.event.event_id,
                    "block_number": 100,
                    "block_hash": "0x" + "ab" * 32,
                    "claim_or_refund_gas_modeled": False,
                },
            )
        )
        with pytest.raises(ValueError, match="not directly chained"):
            build_shadow_economic_evidence_artifact(store, generated_at_ns=4_000)
