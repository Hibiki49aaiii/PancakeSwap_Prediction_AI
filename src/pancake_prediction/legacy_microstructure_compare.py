from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from .clickhouse import ClickHouseParameterizedJsonSource
from .legacy_benchmark import run_legacy_economic_benchmark
from .legacy_campaign import (
    LegacySupportingCampaignConfig,
    run_legacy_supporting_campaign,
)
from .legacy_features import build_legacy_clickhouse_feature_rows
from .legacy_microstructure import (
    MICROSTRUCTURE_HORIZONS_MS,
    LegacyMicrostructureBuildResult,
    enrich_legacy_microstructure_rows,
)
from .legacy_microstructure_model import run_legacy_microstructure_v2_model
from .legacy_model import legacy_oos_to_backtest_signals
from .legacy_pool_projection import build_legacy_absolute_pool_projections
from .legacy_rounds import LegacyRoundAuditReport, LegacyRoundRecord


def _metric_delta(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    return after - before


@dataclass(frozen=True, slots=True)
class LegacyMicrostructureComparisonReport:
    authoritative: bool
    source_class: str
    baseline_campaign_digest: str
    baseline_evaluation_digest: str
    microstructure_build: LegacyMicrostructureBuildResult
    v1_probability: dict[str, object]
    v2_probability: dict[str, object]
    probability_delta: dict[str, float | None]
    v1_economics: dict[str, object]
    v2_economics: dict[str, object]
    v2_feature_names: tuple[str, ...]
    v2_brier_skill_improved: bool
    v2_positive_oos_skill: bool
    v2_pnl_improved: bool
    v2_positive_pnl: bool
    candidate_for_authoritative_retest: bool

    def payload(self) -> dict[str, object]:
        return {
            "authoritative": self.authoritative,
            "source_class": self.source_class,
            "baseline_campaign_digest": self.baseline_campaign_digest,
            "baseline_evaluation_digest": self.baseline_evaluation_digest,
            "microstructure_build": self.microstructure_build.as_dict(),
            "v1_probability": self.v1_probability,
            "v2_probability": self.v2_probability,
            "probability_delta": self.probability_delta,
            "v1_economics": self.v1_economics,
            "v2_economics": self.v2_economics,
            "v2_feature_names": self.v2_feature_names,
            "v2_brier_skill_improved": self.v2_brier_skill_improved,
            "v2_positive_oos_skill": self.v2_positive_oos_skill,
            "v2_pnl_improved": self.v2_pnl_improved,
            "v2_positive_pnl": self.v2_positive_pnl,
            "candidate_for_authoritative_retest": self.candidate_for_authoritative_retest,
            "profitability_gate_eligible": False,
            "scenario_only": True,
        }

    @property
    def comparison_digest(self) -> str:
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
        payload["comparison_digest"] = self.comparison_digest
        return payload


def run_legacy_microstructure_comparison(
    rounds: tuple[LegacyRoundRecord, ...],
    audit: LegacyRoundAuditReport,
    source: ClickHouseParameterizedJsonSource,
    config: LegacySupportingCampaignConfig,
) -> LegacyMicrostructureComparisonReport:
    config.validate()
    max_horizon = max(MICROSTRUCTURE_HORIZONS_MS)
    if config.features.flow_lookback_ms < max_horizon:
        raise ValueError(
            "baseline source envelope must cover the longest microstructure horizon"
        )

    ordered = tuple(sorted(rounds, key=lambda record: record.epoch))
    baseline = run_legacy_supporting_campaign(ordered, audit, source, config)
    base_features = build_legacy_clickhouse_feature_rows(
        ordered,
        source,
        spot_timestamp_unit=config.features.spot_timestamp_unit,
        spot_availability_lag_ms=config.features.spot_availability_lag_ms,
        perp_timestamp_unit=config.features.perp_timestamp_unit,
        perp_availability_lag_ms=config.features.perp_availability_lag_ms,
        include_perp=config.features.include_perp,
        chunk_span_ms=config.features.chunk_span_ms,
        feature_lead_seconds=config.features.feature_lead_seconds,
        flow_lookback_ms=config.features.flow_lookback_ms,
        max_spot_age_ms=config.features.max_spot_age_ms,
        max_perp_age_ms=config.features.max_perp_age_ms,
    )
    microstructure = enrich_legacy_microstructure_rows(
        base_features.rows,
        source,
        spot_timestamp_unit=config.features.spot_timestamp_unit,
        spot_availability_lag_ms=config.features.spot_availability_lag_ms,
        perp_timestamp_unit=config.features.perp_timestamp_unit,
        perp_availability_lag_ms=config.features.perp_availability_lag_ms,
        include_perp=config.features.include_perp,
        chunk_span_ms=config.features.chunk_span_ms,
    )
    if len(microstructure.rows) != len(base_features.rows):
        raise ValueError("microstructure enrichment changed the feature-row envelope")

    v2_model = run_legacy_microstructure_v2_model(
        ordered,
        microstructure.rows,
        min_train_rounds=config.model.min_train_rounds,
        test_rounds=config.model.test_rounds,
        purge_rounds=config.model.purge_rounds,
        embargo_rounds=config.model.embargo_rounds,
        calibration_rounds=config.model.calibration_rounds,
        calibration_bins=config.model.calibration_bins,
        calibration_shrinkage=config.model.calibration_shrinkage,
    )
    projections = build_legacy_absolute_pool_projections(
        ordered,
        decision_lead_seconds=config.features.feature_lead_seconds,
        config=config.pool,
    )
    v2_economics = run_legacy_economic_benchmark(
        ordered,
        legacy_oos_to_backtest_signals(v2_model.signals),
        projections,
        config.economics,
    )

    v1_probability = baseline.probability_metrics.as_dict()
    v2_probability = v2_model.metrics.as_dict()
    v1_brier_skill = baseline.probability_metrics.brier_skill_score
    v2_brier_skill = v2_model.metrics.brier_skill_score
    v1_pnl = cast(int, baseline.economic_summary["pnl_wei"])
    v2_summary = v2_economics.summary()
    v2_pnl = v2_economics.pnl_wei
    brier_improved = (
        v1_brier_skill is not None
        and v2_brier_skill is not None
        and v2_brier_skill > v1_brier_skill
    )
    positive_skill = v2_brier_skill is not None and v2_brier_skill > 0.0
    pnl_improved = v2_pnl > v1_pnl
    positive_pnl = v2_pnl > 0

    return LegacyMicrostructureComparisonReport(
        authoritative=False,
        source_class="third_party_historical_benchmark",
        baseline_campaign_digest=baseline.manifest.digest,
        baseline_evaluation_digest=baseline.evaluation_digest,
        microstructure_build=microstructure,
        v1_probability=v1_probability,
        v2_probability=v2_probability,
        probability_delta={
            "brier_score": _metric_delta(
                v2_model.metrics.brier_score,
                baseline.probability_metrics.brier_score,
            ),
            "brier_skill_score": _metric_delta(v2_brier_skill, v1_brier_skill),
            "log_loss": _metric_delta(
                v2_model.metrics.log_loss,
                baseline.probability_metrics.log_loss,
            ),
            "ece_10": _metric_delta(
                v2_model.metrics.ece_10,
                baseline.probability_metrics.ece_10,
            ),
            "accuracy": _metric_delta(
                v2_model.metrics.accuracy,
                baseline.probability_metrics.accuracy,
            ),
        },
        v1_economics=baseline.economic_summary,
        v2_economics=v2_summary,
        v2_feature_names=v2_model.feature_names,
        v2_brier_skill_improved=brier_improved,
        v2_positive_oos_skill=positive_skill,
        v2_pnl_improved=pnl_improved,
        v2_positive_pnl=positive_pnl,
        candidate_for_authoritative_retest=(
            brier_improved and positive_skill and pnl_improved and positive_pnl
        ),
    )
