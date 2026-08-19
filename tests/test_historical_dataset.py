from __future__ import annotations

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_dataset import (
    backfill_round_decision_snapshots,
    build_portable_round_examples,
)
from pancake_prediction_ai.onchain_collector import PinnedProtocolSnapshot
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.portable_features import PortableFeaturePolicy
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event
from pancake_prediction_ai.round_history import RoundTimeline
from pancake_prediction_ai.rpc_snapshot import BlockAnchor
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_oracle_reference, normalize_round_snapshot
from pancake_prediction_ai.economics import Outcome


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
ORACLE = "0x1111111111111111111111111111111111111111"


def _lifecycle(kind: str, epoch: int, time_s: int, observed_s: int, price=None) -> EventRecord:
    return EventRecord(
        event_id=f"{epoch}:{kind}:{time_s}",
        source="pancake_prediction",
        topic="prediction.round_lifecycle",
        event_time_ns=time_s * 1_000_000_000,
        observed_at_ns=observed_s * 1_000_000_000,
        payload={"kind": kind, "epoch": epoch, "price": price},
    )


def _timeline(epoch: int = 7) -> RoundTimeline:
    start = _lifecycle("START", epoch, 100, 101)
    lock = _lifecycle("LOCK", epoch, 200, 201, 600_00000000)
    end = _lifecycle("END", epoch, 300, 301, 601_00000000)
    return RoundTimeline(
        epoch=epoch,
        start_event=start,
        lock_event=lock,
        end_event=end,
        lock_price=600_00000000,
        close_price=601_00000000,
        outcome=Outcome.BULL,
        lock_available_at_ns=lock.observed_at_ns,
        label_available_at_ns=end.observed_at_ns,
    )


def _source_snapshot(anchor: BlockAnchor, *, epoch: int, captured_ns: int) -> PinnedProtocolSnapshot:
    round_state = PredictionRoundState(
        epoch=epoch,
        start_timestamp=100,
        lock_timestamp=200,
        close_timestamp=300,
        lock_price=0,
        close_price=0,
        lock_oracle_id=0,
        close_oracle_id=0,
        total_amount_wei=300,
        bull_amount_wei=120,
        bear_amount_wei=180,
        reward_base_cal_amount_wei=0,
        reward_amount_wei=0,
        oracle_called=False,
    )
    round_event = normalize_round_snapshot(
        round_state,
        contract_address=PREDICTION,
        treasury_fee_units=300,
        block_number=anchor.number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=captured_ns,
    )
    oracle_ref = normalize_oracle_reference(
        ORACLE,
        contract_address=PREDICTION,
        block_number=anchor.number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=captured_ns,
    )
    chainlink = normalize_latest_round_data(
        (123, 599_00000000, 160, 165, 123),
        decimals=8,
        feed_address=ORACLE,
        observed_at_ns=captured_ns,
        description="BNB / USD",
    )
    return PinnedProtocolSnapshot(
        anchor=anchor,
        current_epoch=epoch,
        treasury_fee_units=300,
        oracle_address=ORACLE,
        oracle_decimals=8,
        oracle_description="BNB / USD",
        round_state=round_state,
        events=(round_event, oracle_ref, chainlink),
    )


class ChainOnly:
    def chain_id(self):
        return 56


def test_decision_snapshot_search_subtracts_latency_before_block_selection(tmp_path, monkeypatch) -> None:
    timeline = _timeline()
    seen = {}
    anchor = BlockAnchor(50, "0x" + "ab" * 32, 177)

    def fake_find(client, *, target_timestamp_s, lower_block, upper_block):
        seen["target"] = target_timestamp_s
        return anchor

    def fake_collect(client, *, anchor, prediction_contract):
        return _source_snapshot(anchor, epoch=7, captured_ns=9_000_000_000_000)

    monkeypatch.setattr(
        "pancake_prediction_ai.historical_dataset.find_block_at_or_before_timestamp",
        fake_find,
    )
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_dataset.collect_protocol_snapshot_at_anchor",
        fake_collect,
    )

    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        result = backfill_round_decision_snapshots(
            ChainOnly(),  # type: ignore[arg-type]
            store,
            [timeline],
            dataset_id="round-dataset-v1",
            decision_lead_ns=20_000_000_000,
            assumed_onchain_latency_ns=2_000_000_000,
            lower_block=1,
            upper_block=100,
        )
        assert seen["target"] == 178
        assert len(result.points) == 1
        point = result.points[0]
        assert point.decision_cutoff_ns == 180_000_000_000
        assert point.reconstructed_observed_at_ns == 179_000_000_000
        assert point.reconstructed_observed_at_ns <= point.decision_cutoff_ns
        assert len(store.read_all_ingest_order()) == 3
        assert store.verify_chain()


def _reconstructed_trade(trade_id: int, time_s: int, price: str):
    raw = normalize_rest_agg_trade(
        {"a": trade_id, "p": price, "q": "1", "f": trade_id, "l": trade_id, "T": time_s * 1000, "m": False},
        symbol="BNBUSDT",
        observed_at_ns=9_000_000_000_000,
    )
    return reconstruct_event(
        raw,
        policy=ReconstructionPolicy("round-dataset-v1", 500_000_000, 9_000_000_000_000),
    )


def test_reconstructed_store_builds_portable_training_example_at_round_cutoff(tmp_path) -> None:
    timeline = _timeline()
    anchor = BlockAnchor(50, "0x" + "ab" * 32, 175)
    source = _source_snapshot(anchor, epoch=7, captured_ns=9_000_000_000_000)
    reconstructed_protocol = tuple(
        reconstruct_event(
            event,
            policy=ReconstructionPolicy("round-dataset-v1", 1_000_000_000, 9_000_000_000_000),
            availability_base_ns=175_000_000_000,
            availability_basis="block_timestamp",
        )
        for event in source.events
    )

    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        store.append_many((_reconstructed_trade(1, 160, "598"), _reconstructed_trade(2, 170, "600")))
        store.append_many(reconstructed_protocol)
        result = build_portable_round_examples(
            store,
            [timeline],
            decision_lead_ns=20_000_000_000,
            feature_policy=PortableFeaturePolicy(
                long_window_ns=30_000_000_000,
                short_window_ns=15_000_000_000,
            ),
        )

    assert len(result.examples) == 1
    assert result.skipped == ()
    example = result.examples[0]
    assert example.round_id == 7
    assert example.decision_cutoff_ns == 180_000_000_000
    assert example.label_available_at_ns == 301_000_000_000
    assert example.outcome is Outcome.BULL
    features = example.feature_dict()
    assert features["binance_last_trade_price"] == 600.0
    assert features["chainlink_price"] == 599.0
    assert features["time_to_lock_seconds"] == 20.0
    assert tuple(features) == result.feature_names


def test_missing_decision_protocol_snapshot_is_explicit_skip(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        result = build_portable_round_examples(
            store,
            [_timeline()],
            decision_lead_ns=20_000_000_000,
        )
    assert result.examples == ()
    assert result.skipped[0].reason == "missing_protocol_snapshot"
