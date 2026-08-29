from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from pancake_prediction import shadow_runtime
from pancake_prediction.binance_live import (
    BinanceLiveCoverage,
    BinanceLiveSyncReport,
    BinanceRestPage,
    LiveVenue,
)
from pancake_prediction.clickhouse import ClickHouseInsertReport, QueryParameter
from pancake_prediction.contracts import MARKETS
from pancake_prediction.replay import ReplaySnapshot
from pancake_prediction.research_ledger import ResearchPredictionRecord
from pancake_prediction.shadow_chain_sync import ShadowChainSyncReport
from pancake_prediction.shadow_inference import ShadowInferenceConfig, ShadowTargetSelection
from pancake_prediction.shadow_ledger import ShadowLedgerStore


class FakeRpc:
    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return 1_100

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "parentHash": "0x" + f"{max(0, number - 1):064x}",
            "timestamp": hex(1_700_000_000 + number),
        }

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        return "0x" + "00" * 32


class FakeClickHouse:
    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        return iter(())

    def insert_json_rows(
        self,
        table: str,
        rows: Iterable[Mapping[str, object]],
        *,
        batch_size: int,
    ) -> ClickHouseInsertReport:
        materialized = tuple(rows)
        return ClickHouseInsertReport(
            batches=1 if materialized else 0,
            rows=len(materialized),
        )


class FakeRest:
    def fetch_agg_trades(
        self,
        *,
        venue: str,
        symbol: str,
        parameters: Mapping[str, int | str],
    ) -> BinanceRestPage:
        return BinanceRestPage(
            rows=(),
            observed_at_ms=1_000_000,
            source_sha256="a" * 64,
        )


@dataclass(frozen=True)
class FakeSchema:
    ready: bool = True


@dataclass(frozen=True)
class FakeInputs:
    replay: ReplaySnapshot
    events: tuple[object, ...]


@dataclass(frozen=True)
class FakeDatasetRows:
    research_feature_rows: tuple[object, ...]


@dataclass(frozen=True)
class FakeChunkedDataset:
    dataset: FakeDatasetRows

    def as_dict(self) -> dict[str, object]:
        return {"research_feature_rows": len(self.dataset.research_feature_rows)}


@dataclass(frozen=True)
class FakeInference:
    prediction: ResearchPredictionRecord

    def as_dict(self) -> dict[str, object]:
        return {
            "prediction": self.prediction.canonical_payload(),
            "signing_enabled": False,
            "live_broadcast": False,
        }


def _chain_report() -> ShadowChainSyncReport:
    return ShadowChainSyncReport(
        market="BNBUSD",
        database="canonical.sqlite3",
        head_block=1_100,
        safe_head_block=1_097,
        previous_last_collected_block=1_096,
        from_block=1_033,
        to_block=1_097,
        confirmations=3,
        prediction_events_inserted=2,
        chainlink_events_inserted=1,
        reorg_blocks_detected=(),
        oracle_proxy="0x" + "11" * 20,
        chainlink_aggregator="0x" + "22" * 20,
        oracle_stability_proof={"method": "test"},
        no_new_confirmed_blocks=False,
    )


def _binance_report(venue: LiveVenue) -> BinanceLiveSyncReport:
    return BinanceLiveSyncReport(
        market="BNBUSD",
        venue=venue,
        symbol="BNBUSDT",
        availability_lag_ms=250,
        timestamp_unit="milliseconds",
        ingest_version=1,
        pages=1,
        batches=1,
        rows=5,
        bootstrap_start_timestamp_ms=None,
        requested_end_timestamp_ms=None,
        resumed_from_aggregate_trade_id=10,
        first_aggregate_trade_id=10,
        last_aggregate_trade_id=14,
        first_trade_timestamp_ms=900_000,
        last_trade_timestamp_ms=999_000,
        first_available_at_ms=900_250,
        last_available_at_ms=999_250,
        response_chain_sha256="b" * 64,
    )


def _coverage(
    venue: LiveVenue,
    *,
    first_available_at_ms: int = 900_000,
) -> BinanceLiveCoverage:
    return BinanceLiveCoverage(
        market="BNBUSD",
        venue=venue,
        symbol="BNBUSDT",
        timestamp_unit="auto" if venue == "spot" else "milliseconds",
        availability_lag_ms=250,
        row_count=100,
        first_available_at_ms=first_available_at_ms,
        last_available_at_ms=999_000,
    )


def _prediction() -> ResearchPredictionRecord:
    return ResearchPredictionRecord(
        market="BNBUSD",
        epoch=10,
        decision_timestamp_ms=1_000_000,
        model_id="runtime-test",
        feature_set_id="full-v1",
        raw_probability_ppm=600_000,
        calibrated_probability_ppm=590_000,
        expected_value_wei=10,
        action="bull",
        feature_digest="c" * 64,
        train_max_epoch=7,
        metadata={
            "stake_wei": 100,
            "bet_gas_wei": 2,
            "claim_gas_wei": 1,
            "treasury_fee_bps": 300,
        },
    )


def _assert_timing_consistent(
    report: shadow_runtime.ShadowRuntimeCycleReport,
) -> None:
    assert report.total_duration_ms >= 0
    assert all(value >= 0 for value in report.phase_durations_ms.values())
    assert report.total_duration_ms >= sum(report.phase_durations_ms.values())
    payload = report.as_dict()
    timing = payload["timing"]
    assert isinstance(timing, dict)
    assert timing["clock"] == "monotonic_perf_counter"
    assert timing["total_duration_ms"] == report.total_duration_ms


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    replay: ReplaySnapshot,
) -> None:
    monkeypatch.setattr(
        shadow_runtime,
        "inspect_binance_trade_schema",
        lambda source: FakeSchema(),
    )
    monkeypatch.setattr(
        shadow_runtime,
        "sync_shadow_chain",
        lambda *args, **kwargs: _chain_report(),
    )

    def fake_binance(*args: object, **kwargs: object) -> BinanceLiveSyncReport:
        venue = cast(LiveVenue, str(kwargs["venue"]))
        return _binance_report(venue)

    monkeypatch.setattr(
        shadow_runtime,
        "sync_binance_live_aggtrades",
        fake_binance,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "inspect_binance_live_coverage",
        lambda source, **kwargs: _coverage(
            cast(LiveVenue, str(kwargs["venue"]))
        ),
    )
    monkeypatch.setattr(
        shadow_runtime,
        "load_canonical_research_inputs",
        lambda database, market: FakeInputs(replay=replay, events=()),
    )
    monkeypatch.setattr(
        shadow_runtime,
        "required_shadow_feature_epochs",
        lambda *args, **kwargs: (1, 2, 10),
    )


def test_runtime_cycle_skips_heavy_dataset_outside_decision_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    _patch_common(monkeypatch, replay)
    monkeypatch.setattr(
        shadow_runtime,
        "select_shadow_target",
        lambda *args, **kwargs: None,
    )

    def should_not_build(*args: object, **kwargs: object) -> object:
        raise AssertionError("dataset must not build outside a decision window")

    monkeypatch.setattr(
        shadow_runtime,
        "build_chunked_clickhouse_research_dataset",
        should_not_build,
    )

    report = shadow_runtime.run_shadow_runtime_cycle(
        FakeRpc(),
        FakeClickHouse(),
        FakeRest(),
        MARKETS["BNBUSD"],
        tmp_path / "canonical.sqlite3",
        tmp_path / "shadow.sqlite3",
        now_timestamp_ms=1_000_000,
        completion_timestamp_ms=1_000_100,
    )

    assert report.status == "no_eligible_target"
    assert report.dataset is None
    assert report.inference is None
    assert report.ledger_event is None
    assert report.as_dict()["signing_enabled"] is False
    assert report.as_dict()["funded_execution"] is False
    _assert_timing_consistent(report)
    assert "target_selection" in report.phase_durations_ms
    assert "dataset_build" not in report.phase_durations_ms
    assert "inference" not in report.phase_durations_ms
    assert "ledger_append" not in report.phase_durations_ms
    assert "campaign_audit" in report.phase_durations_ms
    assert report.decision_to_completion_ms is None
    assert report.submission_margin_ms is None


def test_runtime_cycle_records_prediction_before_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    _patch_common(monkeypatch, replay)
    target = ShadowTargetSelection(
        epoch=10,
        decision_timestamp=1_000,
        scheduled_lock_timestamp=1_020,
        latest_submission_timestamp=1_018,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "select_shadow_target",
        lambda *args, **kwargs: target,
    )
    dataset = FakeChunkedDataset(FakeDatasetRows((object(),)))
    captured: dict[str, object] = {}

    def fake_dataset(*args: object, **kwargs: object) -> FakeChunkedDataset:
        captured["required_epochs"] = kwargs.get("required_epochs")
        return dataset

    monkeypatch.setattr(
        shadow_runtime,
        "build_chunked_clickhouse_research_dataset",
        fake_dataset,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "build_shadow_inference",
        lambda *args, **kwargs: FakeInference(_prediction()),
    )

    shadow_db = tmp_path / "shadow.sqlite3"
    config = shadow_runtime.ShadowRuntimeConfig(
        inference=ShadowInferenceConfig(
            min_train_rounds=20,
            calibration_rounds=5,
            pool_min_train_rounds=10,
            pool_window_rounds=20,
            stake_wei=100,
            bet_gas_wei=2,
            claim_gas_wei=1,
        )
    )
    report = shadow_runtime.run_shadow_runtime_cycle(
        FakeRpc(),
        FakeClickHouse(),
        FakeRest(),
        MARKETS["BNBUSD"],
        tmp_path / "canonical.sqlite3",
        shadow_db,
        config=config,
        now_timestamp_ms=1_000_000,
        completion_timestamp_ms=1_017_000,
    )

    assert report.status == "prediction_recorded"
    assert report.target == target
    assert report.ledger_event is not None
    assert report.ledger_event.kind == "prediction"
    assert captured["required_epochs"] == (1, 2, 10)
    assert ShadowLedgerStore(shadow_db).audit().prediction_count == 1
    _assert_timing_consistent(report)
    for phase in (
        "required_epoch_plan",
        "dataset_build",
        "inference",
        "deadline_check",
        "ledger_append",
        "campaign_audit",
    ):
        assert phase in report.phase_durations_ms
    assert report.decision_to_completion_ms == 17_000
    assert report.submission_margin_ms == 1_000


def test_runtime_cycle_refuses_prediction_at_submission_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    _patch_common(monkeypatch, replay)
    target = ShadowTargetSelection(
        epoch=10,
        decision_timestamp=1_000,
        scheduled_lock_timestamp=1_020,
        latest_submission_timestamp=1_018,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "select_shadow_target",
        lambda *args, **kwargs: target,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "build_chunked_clickhouse_research_dataset",
        lambda *args, **kwargs: FakeChunkedDataset(
            FakeDatasetRows((object(),))
        ),
    )
    monkeypatch.setattr(
        shadow_runtime,
        "build_shadow_inference",
        lambda *args, **kwargs: FakeInference(_prediction()),
    )

    shadow_db = tmp_path / "shadow.sqlite3"
    report = shadow_runtime.run_shadow_runtime_cycle(
        FakeRpc(),
        FakeClickHouse(),
        FakeRest(),
        MARKETS["BNBUSD"],
        tmp_path / "canonical.sqlite3",
        shadow_db,
        now_timestamp_ms=1_000_000,
        completion_timestamp_ms=1_018_000,
    )

    assert report.status == "missed_submission_deadline"
    assert report.ledger_event is None
    assert ShadowLedgerStore(shadow_db).audit().prediction_count == 0
    _assert_timing_consistent(report)
    assert "dataset_build" in report.phase_durations_ms
    assert "inference" in report.phase_durations_ms
    assert "deadline_check" in report.phase_durations_ms
    assert "ledger_append" not in report.phase_durations_ms
    assert report.decision_to_completion_ms == 18_000
    assert report.submission_margin_ms == 0


def test_runtime_cycle_surfaces_target_warmup_as_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    _patch_common(monkeypatch, replay)
    target = ShadowTargetSelection(
        epoch=10,
        decision_timestamp=1_000,
        scheduled_lock_timestamp=1_020,
        latest_submission_timestamp=1_018,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "select_shadow_target",
        lambda *args, **kwargs: target,
    )
    monkeypatch.setattr(
        shadow_runtime,
        "build_chunked_clickhouse_research_dataset",
        lambda *args, **kwargs: FakeChunkedDataset(FakeDatasetRows(())),
    )

    def not_ready(*args: object, **kwargs: object) -> object:
        raise ValueError("target feature row is not prospectively available")

    monkeypatch.setattr(shadow_runtime, "build_shadow_inference", not_ready)

    report = shadow_runtime.run_shadow_runtime_cycle(
        FakeRpc(),
        FakeClickHouse(),
        FakeRest(),
        MARKETS["BNBUSD"],
        tmp_path / "canonical.sqlite3",
        tmp_path / "shadow.sqlite3",
        now_timestamp_ms=1_000_000,
        completion_timestamp_ms=1_005_000,
    )

    assert report.status == "target_not_ready"
    assert report.reason == "target feature row is not prospectively available"
    assert report.ledger_event is None
    _assert_timing_consistent(report)
    assert "dataset_build" in report.phase_durations_ms
    assert "inference" in report.phase_durations_ms
    assert "deadline_check" not in report.phase_durations_ms
    assert "ledger_append" not in report.phase_durations_ms
    assert report.decision_to_completion_ms == 5_000
    assert report.submission_margin_ms == 13_000

def test_runtime_cycle_blocks_prediction_until_live_flow_warmup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    _patch_common(monkeypatch, replay)
    monkeypatch.setattr(
        shadow_runtime,
        "inspect_binance_live_coverage",
        lambda source, **kwargs: _coverage(
            cast(LiveVenue, str(kwargs["venue"])),
            first_available_at_ms=970_000,
        ),
    )

    def should_not_select(*args: object, **kwargs: object) -> object:
        raise AssertionError("target selection must wait for prospective warmup")

    monkeypatch.setattr(shadow_runtime, "select_shadow_target", should_not_select)

    report = shadow_runtime.run_shadow_runtime_cycle(
        FakeRpc(),
        FakeClickHouse(),
        FakeRest(),
        MARKETS["BNBUSD"],
        tmp_path / "canonical.sqlite3",
        tmp_path / "shadow.sqlite3",
        now_timestamp_ms=1_000_000,
        completion_timestamp_ms=1_000_100,
    )

    assert report.status == "source_warmup"
    assert report.reason == "prospective live warmup incomplete: spot,perp"
    assert report.spot_coverage.first_available_at_ms == 970_000
    assert report.ledger_event is None
    _assert_timing_consistent(report)
    assert "source_warmup_check" in report.phase_durations_ms
    assert "target_selection" not in report.phase_durations_ms
    assert "required_epoch_plan" not in report.phase_durations_ms
    assert "dataset_build" not in report.phase_durations_ms
    assert "inference" not in report.phase_durations_ms
    assert report.decision_to_completion_ms is None
    assert report.submission_margin_ms is None

