from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.historical_pipeline import HistoricalPipeline, HistoricalPipelineConfig
from pancake_prediction_ai.historical_store import reconstruction_subgraph_latency_ns
from pancake_prediction_ai.historical_subgraph import (
    _validate_round_bets,
    backfill_subgraph_bets,
    backfill_subgraph_decision_snapshots,
    backfill_subgraph_lifecycle,
    build_subgraph_config_proof,
)
from pancake_prediction_ai.historical_public_rpc import PredictionStaticConfig
from pancake_prediction_ai.prediction_subgraph import PredictionSubgraphBet, PredictionSubgraphRound


ORACLE = "0x" + "12" * 20


def _round() -> PredictionSubgraphRound:
    return PredictionSubgraphRound(
        id="42",
        epoch=42,
        start_at_s=1_000,
        start_block=10_000,
        start_hash="0x" + "11" * 32,
        lock_at_s=1_308,  # operator 8 seconds late; scheduled lock is 1300
        lock_block=10_100,
        lock_hash="0x" + "22" * 32,
        lock_price=Decimal("600.00000000"),
        lock_round_id=50_000,
        close_at_s=1_610,
        close_block=10_200,
        close_hash="0x" + "33" * 32,
        close_price=Decimal("601.00000000"),
        close_round_id=50_100,
        total_bets=3,
        total_amount_wei=3_000_000_000_000_000_000,
        bull_bets=2,
        bull_amount_wei=2_000_000_000_000_000_000,
        bear_bets=1,
        bear_amount_wei=1_000_000_000_000_000_000,
        position="Bull",
        failed=False,
    )


def _bet(identifier: str, created: int, block: int, position: str) -> PredictionSubgraphBet:
    return PredictionSubgraphBet(
        id=identifier,
        epoch=42,
        user="0x" + ("ab" if position == "Bull" else "cd") * 20,
        transaction_hash="0x" + identifier.encode().hex().ljust(64, "0")[:64],
        amount_wei=1_000_000_000_000_000_000,
        position=position,
        created_at_s=created,
        updated_at_s=created,
        block_number=block,
    )


def _bets() -> tuple[PredictionSubgraphBet, ...]:
    return (
        _bet("a", 1_280, 10_080, "Bull"),
        _bet("b", 1_289, 10_089, "Bull"),
        _bet("c", 1_290, 10_090, "Bear"),
    )


def _second_round_and_bets() -> tuple[PredictionSubgraphRound, tuple[PredictionSubgraphBet, ...]]:
    round_ = replace(
        _round(),
        id="43",
        epoch=43,
        start_at_s=1_310,
        start_block=10_210,
        start_hash="0x" + "44" * 32,
        lock_at_s=1_618,
        lock_block=10_310,
        lock_hash="0x" + "55" * 32,
        lock_price=Decimal("601.00000000"),
        lock_round_id=50_001,
        close_at_s=1_920,
        close_block=10_410,
        close_hash="0x" + "66" * 32,
        close_price=Decimal("602.00000000"),
        close_round_id=50_101,
    )
    bets = (
        replace(_bet("d", 1_590, 10_290, "Bull"), epoch=43),
        replace(_bet("e", 1_599, 10_299, "Bull"), epoch=43),
        replace(_bet("f", 1_600, 10_300, "Bear"), epoch=43),
    )
    return round_, bets


def _static() -> PredictionStaticConfig:
    return PredictionStaticConfig(
        head_block=20_000,
        interval_seconds=300,
        treasury_fee_units=200,
        oracle_address=ORACLE,
        oracle_decimals=8,
        oracle_description="BNB / USD",
        captured_at_ns=9_999_000_000_000,
    )


def _config(**kwargs) -> HistoricalPipelineConfig:
    return HistoricalPipelineConfig(
        dataset_id="graph-v1",
        decision_lead_ns=10_000_000_000,
        assumed_binance_latency_ns=500_000_000,
        assumed_onchain_latency_ns=1_000_000_000,
        **kwargs,
    )


def test_subgraph_lifecycle_uses_actual_label_availability_but_scheduled_decision_lock(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        pipeline = HistoricalPipeline(
            store,
            _config(prediction_interval_seconds=300, assumed_subgraph_latency_ns=1_000_000_000),
        )
        appended = backfill_subgraph_lifecycle(
            store,
            (_round(),),
            dataset_id="graph-v1",
            interval_seconds=300,
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=9_999_000_000_000,
            meta_block=20_000,
        )
        assert appended == 3
        timeline = pipeline.timelines().completed[0]
        assert timeline.lock_event.event_time_ns == 1_308_000_000_000
        assert timeline.lock_timestamp_ns == 1_300_000_000_000
        assert timeline.label_available_at_ns == 1_611_000_000_000
        assert timeline.outcome.value == "BULL"
        assert reconstruction_subgraph_latency_ns(store) == 1_000_000_000


def test_final_subgraph_bets_must_reconcile_to_round_totals() -> None:
    _validate_round_bets(_round(), _bets())
    with pytest.raises(ValueError, match="counts do not reconcile"):
        _validate_round_bets(_round(), _bets()[:-1])


def test_decision_snapshot_excludes_bet_not_available_by_cutoff(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_subgraph._oracle_round_at_or_before_cutoff",
        lambda *args, **kwargs: (49_999, 60_050_000_000, 1_275, 1_280, 49_999),
    )
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        pipeline = HistoricalPipeline(
            store,
            _config(prediction_interval_seconds=300, assumed_subgraph_latency_ns=1_000_000_000),
        )
        backfill_subgraph_lifecycle(
            store,
            (_round(),),
            dataset_id="graph-v1",
            interval_seconds=300,
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=_static().captured_at_ns,
            meta_block=20_000,
        )
        backfill_subgraph_bets(
            store,
            (_round(),),
            {42: _bets()},
            dataset_id="graph-v1",
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=_static().captured_at_ns,
            meta_block=20_000,
        )
        timeline = pipeline.timelines().completed[0]
        result = backfill_subgraph_decision_snapshots(
            object(),  # type: ignore[arg-type]
            store,
            (timeline,),
            {42: _round()},
            {42: _bets()},
            dataset_id="graph-v1",
            static_config=_static(),
            decision_lead_ns=10_000_000_000,
            assumed_onchain_latency_ns=1_000_000_000,
            assumed_subgraph_latency_ns=1_000_000_000,
        )
        assert len(result.points) == 1
        snapshots = [
            item.event
            for item in store.read_all_ingest_order()
            if item.event.topic == "prediction.round_snapshot"
        ]
        assert len(snapshots) == 1
        snapshot = snapshots[0]
        # cutoff = 1290. Bet b at 1289 is available exactly at 1290;
        # Bet c at 1290 becomes available at 1291 and must be excluded.
        assert snapshot.payload["bull_amount_wei"] == 2_000_000_000_000_000_000
        assert snapshot.payload["bear_amount_wei"] == 0
        assert snapshot.payload["total_amount_wei"] == 2_000_000_000_000_000_000
        assert snapshot.payload["lock_timestamp"] == 1_300
        reconstruction = snapshot.payload["historical_reconstruction"]
        assert reconstruction["eligible_bet_count"] == 2
        assert snapshot.observed_at_ns == 1_290_000_000_000


def test_reused_chainlink_round_is_persisted_once_across_two_decisions(tmp_path, monkeypatch) -> None:
    shared_oracle = (49_999, 60_050_000_000, 1_275, 1_280, 49_999)
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_subgraph._oracle_round_at_or_before_cutoff",
        lambda *args, **kwargs: shared_oracle,
    )
    second_round, second_bets = _second_round_and_bets()
    rounds = (_round(), second_round)
    bets = {42: _bets(), 43: second_bets}
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        pipeline = HistoricalPipeline(
            store,
            _config(prediction_interval_seconds=300, assumed_subgraph_latency_ns=1_000_000_000),
        )
        backfill_subgraph_lifecycle(
            store,
            rounds,
            dataset_id="graph-v1",
            interval_seconds=300,
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=_static().captured_at_ns,
            meta_block=20_000,
        )
        backfill_subgraph_bets(
            store,
            rounds,
            bets,
            dataset_id="graph-v1",
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=_static().captured_at_ns,
            meta_block=20_000,
        )
        result = backfill_subgraph_decision_snapshots(
            object(),  # type: ignore[arg-type]
            store,
            pipeline.timelines().completed,
            {42: rounds[0], 43: rounds[1]},
            bets,
            dataset_id="graph-v1",
            static_config=_static(),
            decision_lead_ns=10_000_000_000,
            assumed_onchain_latency_ns=1_000_000_000,
            assumed_subgraph_latency_ns=1_000_000_000,
        )
        assert len(result.points) == 2
        stored = [item.event for item in store.read_all_ingest_order()]
        snapshots = [event for event in stored if event.topic == "prediction.round_snapshot"]
        chainlink = [event for event in stored if event.topic == "oracle.latest_round"]
        assert len(snapshots) == 2
        assert len(chainlink) == 1
        assert store.verify_chain()


def test_subgraph_bet_backfill_is_idempotent(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        HistoricalPipeline(
            store,
            _config(prediction_interval_seconds=300, assumed_subgraph_latency_ns=1_000_000_000),
        )
        first = backfill_subgraph_bets(
            store,
            (_round(),),
            {42: _bets()},
            dataset_id="graph-v1",
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=9_999_000_000_000,
            meta_block=20_000,
        )
        second = backfill_subgraph_bets(
            store,
            (_round(),),
            {42: _bets()},
            dataset_id="graph-v1",
            assumed_subgraph_latency_ns=1_000_000_000,
            captured_at_ns=9_999_000_000_000,
            meta_block=20_000,
        )
        assert first == 3
        assert second == 0
        assert store.verify_chain()


def test_config_proof_rejects_any_change_after_historical_range_start(monkeypatch) -> None:
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_subgraph.read_current_prediction_static_config",
        lambda client: _static(),
    )
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_subgraph.count_relevant_prediction_config_changes",
        lambda *args, **kwargs: 1,
    )
    with pytest.raises(ValueError, match="configuration changes"):
        build_subgraph_config_proof(object(), from_block=10_000)  # type: ignore[arg-type]
