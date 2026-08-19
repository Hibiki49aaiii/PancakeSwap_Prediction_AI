from __future__ import annotations

from pancake_prediction_ai.economics import Outcome
from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_binance import HistoricalBinanceBackfillResult
from pancake_prediction_ai.historical_dataset import (
    DecisionSnapshotBackfillResult,
    HistoricalExampleBuildResult,
)
from pancake_prediction_ai.historical_evidence_run import (
    build_historical_binance_windows,
    run_historical_evidence_acquisition,
)
from pancake_prediction_ai.historical_pipeline import HistoricalPipelineConfig
from pancake_prediction_ai.portable_features import PortableFeaturePolicy
from pancake_prediction_ai.provenance import ReconstructionPolicy, reconstruct_event
from pancake_prediction_ai.round_history import (
    LifecycleBackfillResult,
    RoundTimeline,
    RoundTimelineBuildResult,
)


def _timeline(epoch: int, *, start_s: int, lock_s: int, end_s: int) -> RoundTimeline:
    def event(kind: str, timestamp_s: int, price):
        return EventRecord(
            event_id=f"{epoch}:{kind}",
            source="pancake_prediction",
            topic="prediction.round_lifecycle",
            event_time_ns=timestamp_s * 1_000_000_000,
            observed_at_ns=timestamp_s * 1_000_000_000,
            payload={"kind": kind, "epoch": epoch, "price": price},
        )

    start = event("START", start_s, None)
    lock = event("LOCK", lock_s, 100)
    end = event("END", end_s, 101)
    return RoundTimeline(
        epoch=epoch,
        start_event=start,
        lock_event=lock,
        end_event=end,
        lock_price=100,
        close_price=101,
        outcome=Outcome.BULL,
        lock_available_at_ns=lock.observed_at_ns,
        label_available_at_ns=end.observed_at_ns,
    )


def _config() -> HistoricalPipelineConfig:
    return HistoricalPipelineConfig(
        dataset_id="evidence-v1",
        decision_lead_ns=10_000_000_000,
        assumed_binance_latency_ns=2_000_000_000,
        assumed_onchain_latency_ns=3_000_000_000,
        feature_policy=PortableFeaturePolicy(
            long_window_ns=30_000_000_000,
            short_window_ns=5_000_000_000,
        ),
    )


def test_historical_binance_windows_merge_only_overlapping_feature_intervals() -> None:
    windows = build_historical_binance_windows(
        (
            _timeline(1, start_s=100, lock_s=200, end_s=300),
            _timeline(2, start_s=150, lock_s=230, end_s=330),
            _timeline(3, start_s=400, lock_s=500, end_s=600),
        ),
        _config(),
    )
    assert [(item.start_time_ms, item.end_time_ms, item.epochs) for item in windows] == [
        (160_000, 220_000, (1, 2)),
        (460_000, 490_000, (3,)),
    ]


def test_historical_binance_window_is_based_on_event_time_not_assumed_latency() -> None:
    config = _config()
    window = build_historical_binance_windows(
        (_timeline(1, start_s=100, lock_s=200, end_s=300),),
        config,
    )[0]
    assert window.start_time_ms == 160_000
    assert window.end_time_ms == 190_000


def test_historical_evidence_run_orders_lifecycle_binance_protocol_and_examples(tmp_path, monkeypatch) -> None:
    calls = []
    timeline = _timeline(7, start_s=100, lock_s=200, end_s=300)

    class FakePipeline:
        def __init__(self, store, config):
            self.store = store
            self.config = config

        def backfill_lifecycle(self, client, *, from_block, to_block, chunk_size):
            calls.append(("lifecycle", from_block, to_block, chunk_size))
            raw = EventRecord(
                event_id="marker",
                source="test",
                topic="historical.marker",
                event_time_ns=1,
                observed_at_ns=1,
                payload={"ok": True},
            )
            self.store.append(
                reconstruct_event(
                    raw,
                    policy=ReconstructionPolicy(
                        dataset_id=self.config.dataset_id,
                        assumed_latency_ns=0,
                        captured_at_ns=1,
                    ),
                )
            )
            return LifecycleBackfillResult(self.config.dataset_id, from_block, to_block, 1, 1)

        def timelines(self):
            calls.append(("timelines",))
            return RoundTimelineBuildResult((timeline,), (99,))

        def backfill_binance(self, client, **kwargs):
            calls.append(("binance", kwargs["start_time_ms"], kwargs["end_time_ms"]))
            return HistoricalBinanceBackfillResult(
                self.config.dataset_id,
                0,
                None,
                None,
                None,
                None,
            )

        def backfill_decision_protocol(self, client, *, lower_block, upper_block):
            calls.append(("protocol", lower_block, upper_block))
            return DecisionSnapshotBackfillResult((), (7,))

        def build_examples(self):
            calls.append(("examples",))
            return HistoricalExampleBuildResult((), (), ())

    monkeypatch.setattr(
        "pancake_prediction_ai.historical_evidence_run.HistoricalPipeline",
        FakePipeline,
    )

    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        result = run_historical_evidence_acquisition(
            store,
            config=_config(),
            binance_client=object(),  # type: ignore[arg-type]
            rpc_client=object(),  # type: ignore[arg-type]
            from_block=1000,
            to_block=2000,
            lifecycle_chunk_size=250,
        )
        assert result.completed_rounds == 1
        assert result.incomplete_epochs == (99,)
        assert result.store_event_count == 1
        assert result.store_tip_hash == store.read_all_ingest_order()[-1].event_hash
        assert store.verify_chain()

    assert calls == [
        ("lifecycle", 1000, 2000, 250),
        ("timelines",),
        ("binance", 160_000, 190_000),
        ("protocol", 1000, 2000),
        ("examples",),
    ]
