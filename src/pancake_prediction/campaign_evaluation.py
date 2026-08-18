from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .backtest import BacktestConfig
from .baseline import ResearchFeatureRow, run_walkforward_baseline
from .economic_ablation import EconomicAblationResult, run_economic_feature_ablation
from .economic_oos import run_oos_economic_backtest
from .pool_projection import PoolProjectionBaselineConfig, build_oos_pool_projections
from .replay import ChainEvent, ReplaySnapshot


@dataclass(frozen=True, slots=True)
class EconomicCampaignConfig:
    stake_wei: int
    bet_gas_wei: int
    claim_gas_wei: int
    inclusion_latency_seconds: int
    min_expected_value_wei: int = 0
    decision_lead_seconds: int = 20
    initial_interval_seconds: int = 300
    initial_treasury_fee_bps: int = 300
    initial_buffer_seconds: int = 30
    min_train_rounds: int = 200
    test_rounds: int = 100
    purge_rounds: int = 2
    embargo_rounds: int = 2
    calibration_rounds: int = 50
    pool_min_train_rounds: int = 50
    pool_window_rounds: int = 500
    run_ablation: bool = False

    def validate(self) -> None:
        backtest = self.backtest_config()
        backtest.validate()
        if self.min_train_rounds <= 0 or self.test_rounds <= 0:
            raise ValueError("walk-forward train/test sizes must be positive")
        if self.purge_rounds < 0 or self.embargo_rounds < 0:
            raise ValueError("purge/embargo rounds must be non-negative")
        if self.calibration_rounds < 2:
            raise ValueError("calibration_rounds must be at least 2")
        projection = self.pool_projection_config()
        projection.validate()

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
class EconomicCampaignReport:
    campaign_digest: str
    feature_set_id: str
    config: EconomicCampaignConfig
    probability_metrics: dict[str, object]
    fold_count: int
    calibration_failures: int
    direction_signal_count: int
    pool_projection_count: int
    joint_epoch_count: int
    backtest_summary: dict[str, object]
    ablation: tuple[EconomicAblationResult, ...]

    def payload(self) -> dict[str, object]:
        return {
            "campaign_digest": self.campaign_digest,
            "feature_set_id": self.feature_set_id,
            "config": asdict(self.config),
            "probability_metrics": self.probability_metrics,
            "fold_count": self.fold_count,
            "calibration_failures": self.calibration_failures,
            "direction_signal_count": self.direction_signal_count,
            "pool_projection_count": self.pool_projection_count,
            "joint_epoch_count": self.joint_epoch_count,
            "backtest_summary": self.backtest_summary,
            "ablation": [asdict(item) for item in self.ablation],
        }

    @property
    def evaluation_digest(self) -> str:
        raw = (
            json.dumps(
                self.payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, object]:
        payload = self.payload()
        payload["evaluation_digest"] = self.evaluation_digest
        return payload


def _validate_digest(value: str, *, field: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return normalized


def _backtest_summary(payload: dict[str, object]) -> dict[str, object]:
    summary = dict(payload)
    summary.pop("trades", None)
    return summary


def run_source_bound_economic_campaign(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    rows: Iterable[ResearchFeatureRow],
    *,
    campaign_digest: str,
    config: EconomicCampaignConfig,
    feature_set_id: str = "full-v1",
) -> EconomicCampaignReport:
    normalized_digest = _validate_digest(campaign_digest, field="campaign_digest")
    config.validate()
    cached_rows = tuple(rows)
    baseline = run_walkforward_baseline(
        replay,
        cached_rows,
        feature_set_id=feature_set_id,
        min_train_rounds=config.min_train_rounds,
        test_rounds=config.test_rounds,
        purge_rounds=config.purge_rounds,
        embargo_rounds=config.embargo_rounds,
        calibration_rounds=config.calibration_rounds,
    )
    backtest_config = config.backtest_config()
    projections = build_oos_pool_projections(
        replay,
        events,
        backtest_config,
        config=config.pool_projection_config(),
    )
    economic = run_oos_economic_backtest(
        replay,
        events,
        baseline.signals,
        projections,
        backtest_config,
        feature_set_id=feature_set_id,
        purge_rounds=config.purge_rounds,
    )
    ablation: tuple[EconomicAblationResult, ...] = ()
    if config.run_ablation:
        ablation = run_economic_feature_ablation(
            replay,
            events,
            cached_rows,
            projections,
            backtest_config,
            min_train_rounds=config.min_train_rounds,
            test_rounds=config.test_rounds,
            purge_rounds=config.purge_rounds,
            embargo_rounds=config.embargo_rounds,
            calibration_rounds=config.calibration_rounds,
        )
    return EconomicCampaignReport(
        campaign_digest=normalized_digest,
        feature_set_id=feature_set_id,
        config=config,
        probability_metrics=economic.probability_metrics.as_dict(),
        fold_count=baseline.fold_count,
        calibration_failures=baseline.calibration_failures,
        direction_signal_count=economic.direction_signal_count,
        pool_projection_count=economic.pool_projection_count,
        joint_epoch_count=economic.joint_epoch_count,
        backtest_summary=_backtest_summary(economic.backtest.as_dict()),
        ablation=ablation,
    )
