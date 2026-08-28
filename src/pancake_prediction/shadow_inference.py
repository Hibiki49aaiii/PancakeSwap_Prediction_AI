from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .backtest import BacktestConfig, DecisionSnapshot, PoolProjection, build_decision_snapshot
from .baseline import (
    ALL_FEATURE_NAMES,
    ResearchFeatureRow,
    fit_logistic_baseline,
)
from .calibration import CalibrationPoint, fit_histogram_calibrator
from .economics import PPM, ParimutuelQuote, expected_value_wei
from .pool_projection import PoolProjectionBaselineConfig, build_oos_pool_projections
from .replay import ChainEvent, ReplaySnapshot, RoundRecord
from .research_ledger import ResearchPredictionRecord, feature_digest, validate_research_prediction


@dataclass(frozen=True, slots=True)
class ShadowInferenceConfig:
    feature_set_id: str = "full-v1"
    feature_names: tuple[str, ...] = ALL_FEATURE_NAMES
    min_train_rounds: int = 300
    calibration_rounds: int = 60
    calibration_bins: int = 10
    calibration_shrinkage: int = 20
    purge_rounds: int = 2
    pool_min_train_rounds: int = 150
    pool_window_rounds: int = 400
    stake_wei: int = 10**16
    bet_gas_wei: int = 5 * 10**13
    claim_gas_wei: int = 3 * 10**13
    inclusion_latency_seconds: int = 2
    min_expected_value_wei: int = 0
    decision_lead_seconds: int = 20
    initial_interval_seconds: int = 300
    initial_treasury_fee_bps: int = 300
    initial_buffer_seconds: int = 30

    def validate(self) -> None:
        if not self.feature_set_id:
            raise ValueError("feature_set_id must be non-empty")
        if not self.feature_names:
            raise ValueError("feature_names must be non-empty")
        if self.min_train_rounds <= self.calibration_rounds + 1:
            raise ValueError(
                "min_train_rounds must exceed calibration_rounds by at least two rows"
            )
        if self.calibration_rounds < 2:
            raise ValueError("calibration_rounds must be at least 2")
        if self.calibration_bins <= 1 or self.calibration_shrinkage < 0:
            raise ValueError("invalid calibration configuration")
        if self.purge_rounds < 0:
            raise ValueError("purge_rounds must be non-negative")
        if self.pool_min_train_rounds <= 0:
            raise ValueError("pool_min_train_rounds must be positive")
        if self.pool_window_rounds < self.pool_min_train_rounds:
            raise ValueError("pool_window_rounds must cover pool_min_train_rounds")
        if self.inclusion_latency_seconds >= self.decision_lead_seconds:
            raise ValueError(
                "inclusion_latency_seconds must be less than decision_lead_seconds"
            )
        self.backtest_config().validate()
        self.pool_projection_config().validate()

    def backtest_config(self) -> BacktestConfig:
        return BacktestConfig(
            stake_wei=self.stake_wei,
            initial_interval_seconds=self.initial_interval_seconds,
            initial_treasury_fee_bps=self.initial_treasury_fee_bps,
            initial_buffer_seconds=self.initial_buffer_seconds,
            decision_lead_seconds=self.decision_lead_seconds,
            inclusion_latency_seconds=self.inclusion_latency_seconds,
            bet_gas_wei=self.bet_gas_wei,
            claim_gas_wei=self.claim_gas_wei,
            min_expected_value_wei=self.min_expected_value_wei,
            require_pool_projection=True,
        )

    def pool_projection_config(self) -> PoolProjectionBaselineConfig:
        return PoolProjectionBaselineConfig(
            min_train_rounds=self.pool_min_train_rounds,
            window_rounds=self.pool_window_rounds,
            purge_rounds=self.purge_rounds,
        )


@dataclass(frozen=True, slots=True)
class ShadowInferenceResult:
    prediction: ResearchPredictionRecord
    snapshot: DecisionSnapshot
    projection: PoolProjection
    raw_model_id: str
    calibrator_model_id: str
    training_row_count: int
    fit_row_count: int
    calibration_row_count: int
    bull_expected_value_wei: int
    bear_expected_value_wei: int

    def as_dict(self) -> dict[str, object]:
        return {
            "prediction": self.prediction.canonical_payload(),
            "prediction_digest": self.prediction.digest(),
            "snapshot": asdict(self.snapshot),
            "projection": asdict(self.projection),
            "raw_model_id": self.raw_model_id,
            "calibrator_model_id": self.calibrator_model_id,
            "training_row_count": self.training_row_count,
            "fit_row_count": self.fit_row_count,
            "calibration_row_count": self.calibration_row_count,
            "bull_expected_value_wei": self.bull_expected_value_wei,
            "bear_expected_value_wei": self.bear_expected_value_wei,
            "signing_enabled": False,
            "live_broadcast": False,
        }


def _target_round(replay: ReplaySnapshot, target_epoch: int) -> RoundRecord:
    matches = [record for record in replay.rounds if record.epoch == target_epoch]
    if len(matches) != 1:
        raise ValueError(
            f"target epoch {target_epoch} must appear exactly once in replay"
        )
    return matches[0]


def _target_row(
    rows: tuple[ResearchFeatureRow, ...],
    *,
    market: str,
    target_epoch: int,
) -> ResearchFeatureRow:
    matches = [
        row for row in rows if row.market == market and row.epoch == target_epoch
    ]
    if len(matches) != 1:
        raise ValueError(
            f"target epoch {target_epoch} must have exactly one research feature row"
        )
    return matches[0]


def _eligible_training_rows(
    replay: ReplaySnapshot,
    rows: tuple[ResearchFeatureRow, ...],
    *,
    target_epoch: int,
    target_decision_timestamp: int,
    purge_rounds: int,
) -> tuple[tuple[ResearchFeatureRow, ...], dict[int, int]]:
    latest_allowed_epoch = target_epoch - purge_rounds - 1
    settled: dict[int, RoundRecord] = {}
    outcomes: dict[int, int] = {}
    for record in replay.rounds:
        if (
            record.epoch <= latest_allowed_epoch
            and record.label in {"bull", "bear"}
            and record.end_timestamp is not None
            and record.end_timestamp < target_decision_timestamp
        ):
            if record.epoch in settled:
                raise ValueError(f"duplicate replay epoch {record.epoch}")
            settled[record.epoch] = record
            outcomes[record.epoch] = 1 if record.label == "bull" else 0

    by_epoch: dict[int, ResearchFeatureRow] = {}
    for row in rows:
        if row.market != replay.market or row.epoch not in settled:
            continue
        if row.decision_timestamp_ms >= target_decision_timestamp * 1_000:
            continue
        if row.epoch in by_epoch:
            raise ValueError(f"duplicate training feature row for epoch {row.epoch}")
        by_epoch[row.epoch] = row

    ordered = tuple(by_epoch[epoch] for epoch in sorted(by_epoch))
    return ordered, outcomes


def _quote(
    *,
    side: str,
    snapshot: DecisionSnapshot,
    projection: PoolProjection,
    config: BacktestConfig,
) -> ParimutuelQuote:
    return ParimutuelQuote(
        side=side,
        side_pool_wei=(
            projection.projected_bull_wei
            if side == "bull"
            else projection.projected_bear_wei
        ),
        opposing_pool_wei=(
            projection.projected_bear_wei
            if side == "bull"
            else projection.projected_bull_wei
        ),
        stake_wei=config.stake_wei,
        fee_bps=snapshot.treasury_fee_bps,
        bet_gas_wei=config.bet_gas_wei,
        claim_gas_wei=config.claim_gas_wei,
    )


def build_shadow_inference(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    rows: Iterable[ResearchFeatureRow],
    *,
    target_epoch: int,
    config: ShadowInferenceConfig | None = None,
) -> ShadowInferenceResult:
    selected = config or ShadowInferenceConfig()
    selected.validate()
    backtest_config = selected.backtest_config()
    target = _target_round(replay, target_epoch)
    snapshot = build_decision_snapshot(replay, target, events, backtest_config)
    if snapshot is None:
        raise ValueError("target round has no valid pre-lock decision snapshot")

    cached_rows = tuple(rows)
    target_row = _target_row(
        cached_rows,
        market=replay.market,
        target_epoch=target_epoch,
    )
    expected_decision_ms = snapshot.decision_timestamp * 1_000
    if target_row.decision_timestamp_ms != expected_decision_ms:
        raise ValueError(
            "target feature row decision timestamp does not match canonical decision snapshot"
        )

    training_rows, outcomes = _eligible_training_rows(
        replay,
        cached_rows,
        target_epoch=target_epoch,
        target_decision_timestamp=snapshot.decision_timestamp,
        purge_rounds=selected.purge_rounds,
    )
    if len(training_rows) < selected.min_train_rounds:
        raise ValueError(
            f"insufficient settled training rows: {len(training_rows)} "
            f"< {selected.min_train_rounds}"
        )

    fit_rows = training_rows[: -selected.calibration_rounds]
    calibration_rows = training_rows[-selected.calibration_rounds :]
    model = fit_logistic_baseline(
        fit_rows,
        outcomes,
        feature_names=selected.feature_names,
    )
    calibrator = fit_histogram_calibrator(
        [
            CalibrationPoint(model.predict_ppm(row), outcomes[row.epoch])
            for row in calibration_rows
        ],
        bins=selected.calibration_bins,
        shrinkage=selected.calibration_shrinkage,
        train_max_epoch=calibration_rows[-1].epoch,
        model_id=f"{model.model_id}-shadow-cal",
    )
    raw_probability = model.predict_ppm(target_row)
    calibrated_probability = calibrator.predict_ppm(raw_probability)

    projections = build_oos_pool_projections(
        replay,
        events,
        backtest_config,
        config=selected.pool_projection_config(),
    )
    projection = projections.get(target_epoch)
    if projection is None:
        raise ValueError("target round has no leakage-safe pool projection")
    if projection.generated_at > snapshot.decision_timestamp:
        raise ValueError("target pool projection was generated after decision cutoff")
    latest_allowed_epoch = target_epoch - selected.purge_rounds - 1
    if projection.train_max_epoch is None or projection.train_max_epoch > latest_allowed_epoch:
        raise ValueError("target pool projection violates purge boundary")

    execution_timestamp = (
        snapshot.decision_timestamp + selected.inclusion_latency_seconds
    )
    if execution_timestamp >= snapshot.scheduled_lock_timestamp:
        raise ValueError("configured shadow execution would miss the scheduled lock")

    bull_ev = expected_value_wei(
        _quote(
            side="bull",
            snapshot=snapshot,
            projection=projection,
            config=backtest_config,
        ),
        win_probability_ppm=calibrated_probability,
    )
    bear_ev = expected_value_wei(
        _quote(
            side="bear",
            snapshot=snapshot,
            projection=projection,
            config=backtest_config,
        ),
        win_probability_ppm=PPM - calibrated_probability,
    )
    side, best_ev = ("bull", bull_ev) if bull_ev >= bear_ev else ("bear", bear_ev)
    action = side if best_ev > selected.min_expected_value_wei else "skip"

    train_max_epoch = training_rows[-1].epoch
    record = ResearchPredictionRecord(
        market=replay.market,
        epoch=target_epoch,
        decision_timestamp_ms=expected_decision_ms,
        model_id=f"{model.model_id}+{calibrator.model_id}",
        feature_set_id=selected.feature_set_id,
        raw_probability_ppm=raw_probability,
        calibrated_probability_ppm=calibrated_probability,
        expected_value_wei=best_ev,
        action=action,
        feature_digest=feature_digest(
            {
                "market": target_row.market,
                "epoch": target_row.epoch,
                "decision_timestamp_ms": target_row.decision_timestamp_ms,
                "values": dict(target_row.values),
            }
        ),
        train_max_epoch=train_max_epoch,
        metadata={
            "replay_digest": replay.output_digest,
            "raw_model_id": model.model_id,
            "calibrator_model_id": calibrator.model_id,
            "projection_model_id": projection.model_id,
            "projection_train_max_epoch": projection.train_max_epoch,
            "training_row_count": len(training_rows),
            "fit_row_count": len(fit_rows),
            "calibration_row_count": len(calibration_rows),
            "observed_bull_wei": snapshot.bull_observed_wei,
            "observed_bear_wei": snapshot.bear_observed_wei,
            "projected_bull_wei": projection.projected_bull_wei,
            "projected_bear_wei": projection.projected_bear_wei,
            "treasury_fee_bps": snapshot.treasury_fee_bps,
            "stake_wei": selected.stake_wei,
            "bet_gas_wei": selected.bet_gas_wei,
            "claim_gas_wei": selected.claim_gas_wei,
            "inclusion_latency_seconds": selected.inclusion_latency_seconds,
            "min_expected_value_wei": selected.min_expected_value_wei,
            "bull_expected_value_wei": bull_ev,
            "bear_expected_value_wei": bear_ev,
        },
    )
    validate_research_prediction(record, purge_rounds=selected.purge_rounds)
    return ShadowInferenceResult(
        prediction=record,
        snapshot=snapshot,
        projection=projection,
        raw_model_id=model.model_id,
        calibrator_model_id=calibrator.model_id,
        training_row_count=len(training_rows),
        fit_row_count=len(fit_rows),
        calibration_row_count=len(calibration_rows),
        bull_expected_value_wei=bull_ev,
        bear_expected_value_wei=bear_ev,
    )
