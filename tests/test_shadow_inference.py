from __future__ import annotations

from dataclasses import dataclass

from pancake_prediction_ai.baseline_model import BaselineModel, FeatureScaler
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.shadow_inference import infer_shadow_decision
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
FEED = "0x1111111111111111111111111111111111111111"


FEATURE_NAMES = (
    "binance_last_trade_price",
    "chainlink_price",
    "binance_chainlink_divergence_bps",
    "oracle_age_seconds",
    "last_trade_age_seconds",
    "trade_count_long",
    "trade_count_short",
    "aggressor_flow_ratio_long",
    "aggressor_flow_ratio_short",
    "price_change_bps_long",
    "price_change_bps_short",
    "realized_volatility_bps_long",
    "pancake_bull_share",
    "pancake_pool_imbalance",
    "pancake_log_total_amount",
    "time_to_lock_seconds",
)


@dataclass(frozen=True)
class FakeArtifact:
    model: BaselineModel
    artifact_sha256: str = "aa" * 32
    payload: dict[str, str] | None = None

    def __post_init__(self):
        if self.payload is None:
            object.__setattr__(
                self,
                "payload",
                {"source_dataset_artifact_sha256": "bb" * 32},
            )

    def validate(self) -> None:
        self.model.validate()


def _model(feature_names=FEATURE_NAMES) -> BaselineModel:
    width = len(feature_names) + 1
    return BaselineModel(
        feature_names=tuple(feature_names),
        scaler=FeatureScaler(
            means=tuple(0.0 for _ in feature_names),
            scales=tuple(1.0 for _ in feature_names),
        ),
        weights=(
            tuple([1.0] + [0.0] * (width - 1)),
            tuple([0.0] + [0.0] * (width - 1)),
            tuple([-1.0] + [0.0] * (width - 1)),
        ),
    )


def _trade(trade_id: int, time_s: int) -> EventRecord:
    return normalize_rest_agg_trade(
        {
            "a": trade_id,
            "p": str(598 + trade_id),
            "q": "1",
            "f": trade_id,
            "l": trade_id,
            "T": time_s * 1000,
            "m": trade_id % 2 == 0,
        },
        symbol="BNBUSDT",
        observed_at_ns=time_s * 1_000_000_000 + 100_000_000,
    )


def _round_event() -> EventRecord:
    return normalize_round_snapshot(
        PredictionRoundState(
            epoch=7,
            start_timestamp=60,
            lock_timestamp=120,
            close_timestamp=180,
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
        ),
        contract_address=PREDICTION,
        treasury_fee_units=300,
        block_number=100,
        block_timestamp_s=105,
        observed_at_ns=105_000_000_000,
    )


def _anchor() -> EventRecord:
    return EventRecord(
        event_id="collector:protocol:block_anchor:100:" + "0x" + "ab" * 32,
        source="collector",
        topic="collector.protocol_block_anchor",
        event_time_ns=105_000_000_000,
        observed_at_ns=105_000_000_000,
        payload={
            "chain_id": 56,
            "block_number": 100,
            "block_hash": "0x" + "ab" * 32,
            "parent_hash": "0x" + "cd" * 32,
            "block_timestamp_s": 105,
        },
    )


def _populate(store: EventStore) -> None:
    for trade_id, time_s in enumerate((90, 95, 100, 105, 108), start=1):
        store.append(_trade(trade_id, time_s))
    store.append(
        normalize_latest_round_data(
            (10, 599_00000000, 90, 100, 10),
            decimals=8,
            feed_address=FEED,
            observed_at_ns=101_000_000_000,
        )
    )
    store.append(_anchor())
    store.append(_round_event())


def test_shadow_decision_is_hash_chained_directly_to_exact_source_tip(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        source_tip = store.read_all_ingest_order()[-1].event_hash
        source_count = len(store.read_all_ingest_order())
        result = infer_shadow_decision(
            FakeArtifact(_model()),  # type: ignore[arg-type]
            store,
            clock_ns=lambda: 110_000_000_000,
        )
        assert result.accepted
        assert result.blockers == ()
        assert result.round_id == 7
        assert result.predicted_outcome == "BULL"
        assert result.probability_bull > result.probability_bear > result.probability_tie
        assert result.source_snapshot_tip_hash == source_tip
        assert result.source_snapshot_event_count == source_count
        assert result.source_snapshot_digest is not None
        assert len(result.source_snapshot_digest) == 64
        assert result.stored_decision is not None
        assert result.stored_decision.prev_hash == source_tip
        assert result.stored_decision.event.payload["source_snapshot_tip_hash"] == source_tip
        assert result.stored_decision.event.payload["promoted_model_artifact_sha256"] == "aa" * 32
        assert store.verify_chain()


def test_second_model_decision_for_same_round_is_blocked_without_new_write(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        artifact = FakeArtifact(_model())
        first = infer_shadow_decision(
            artifact,  # type: ignore[arg-type]
            store,
            clock_ns=lambda: 110_000_000_000,
        )
        assert first.accepted
        before = len(store.read_all_ingest_order())
        second = infer_shadow_decision(
            artifact,  # type: ignore[arg-type]
            store,
            clock_ns=lambda: 111_000_000_000,
        )
        assert not second.accepted
        assert second.blockers == ("round_already_decided",)
        assert second.stored_decision is None
        assert len(store.read_all_ingest_order()) == before
        assert store.verify_chain()


def test_protocol_anomaly_blocks_model_decision_and_rolls_back(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        store.append(
            EventRecord(
                event_id="anomaly:1",
                source="collector",
                topic="collector.protocol_anomaly",
                event_time_ns=106_000_000_000,
                observed_at_ns=106_000_000_000,
                payload={"anomaly": "parent_hash_mismatch"},
            )
        )
        before = len(store.read_all_ingest_order())
        result = infer_shadow_decision(
            FakeArtifact(_model()),  # type: ignore[arg-type]
            store,
            clock_ns=lambda: 110_000_000_000,
        )
        assert not result.accepted
        assert "protocol_anomaly_since_round_snapshot" in result.blockers
        assert result.stored_decision is None
        assert len(store.read_all_ingest_order()) == before
        assert not any(
            item.event.topic == "shadow.model_decision"
            for item in store.read_all_ingest_order()
        )


def test_model_feature_schema_mismatch_is_blocked_before_decision_write(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        before = len(store.read_all_ingest_order())
        result = infer_shadow_decision(
            FakeArtifact(_model(("wrong_feature",))),  # type: ignore[arg-type]
            store,
            clock_ns=lambda: 110_000_000_000,
        )
        assert not result.accepted
        assert result.blockers == ("model_feature_schema_mismatch",)
        assert len(store.read_all_ingest_order()) == before


def test_future_observed_store_event_blocks_inference_before_replay(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        _populate(store)
        store.append(
            EventRecord(
                event_id="clock-skew:future",
                source="test",
                topic="clock",
                event_time_ns=100_000_000_000,
                observed_at_ns=120_000_000_000,
                payload={"x": 1},
            )
        )
        before = len(store.read_all_ingest_order())
        result = infer_shadow_decision(
            FakeArtifact(_model()),  # type: ignore[arg-type]
            store,
            clock_ns=lambda: 110_000_000_000,
        )
        assert not result.accepted
        assert result.blockers == ("future_observed_store_event",)
        assert len(store.read_all_ingest_order()) == before


def test_reconstructed_store_is_rejected(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        try:
            infer_shadow_decision(
                FakeArtifact(_model()),  # type: ignore[arg-type]
                store,
                clock_ns=lambda: 110_000_000_000,
            )
        except ValueError as exc:
            assert "observed Event Store" in str(exc)
        else:
            raise AssertionError("reconstructed store should have been rejected")
