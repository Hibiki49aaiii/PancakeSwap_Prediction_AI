from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .absolute_pool_projection import AbsolutePoolProjectionConfig
from .binance_archive import TimestampUnit
from .clickhouse import ClickHouseParameterizedJsonSource
from .clickhouse_manifest import BinanceSourceSlice, load_binance_source_slices
from .legacy_benchmark import (
    LegacyEconomicBenchmarkConfig,
    LegacyEconomicBenchmarkReport,
    run_legacy_economic_benchmark,
)
from .legacy_features import LegacyFeatureBuildResult, build_legacy_clickhouse_feature_rows
from .legacy_model import run_legacy_walkforward_model
from .legacy_pool_projection import build_legacy_absolute_pool_projections
from .legacy_rounds import (
    LEGACY_ROUNDS_SOURCE_CLASS,
    LegacyRoundAuditReport,
    LegacyRoundRecord,
)
from .walkforward import OosMetrics


@dataclass(frozen=True, slots=True)
class LegacyFeatureConfig:
    spot_timestamp_unit: TimestampUnit
    spot_availability_lag_ms: int
    perp_timestamp_unit: TimestampUnit = "milliseconds"
    perp_availability_lag_ms: int = 0
    include_perp: bool = True
    chunk_span_ms: int = 3_600_000
    feature_lead_seconds: int = 20
    flow_lookback_ms: int = 60_000
    max_spot_age_ms: int = 5_000
    max_perp_age_ms: int = 5_000

    def validate(self) -> None:
        if self.spot_availability_lag_ms < 0 or self.perp_availability_lag_ms < 0:
            raise ValueError("Binance availability lag must be non-negative")
        if self.chunk_span_ms <= 0 or self.flow_lookback_ms <= 0:
            raise ValueError("chunk span and flow lookback must be positive")
        if self.feature_lead_seconds <= 0:
            raise ValueError("feature_lead_seconds must be positive")
        if self.max_spot_age_ms <= 0 or self.max_perp_age_ms <= 0:
            raise ValueError("max price ages must be positive")


@dataclass(frozen=True, slots=True)
class LegacyModelConfig:
    min_train_rounds: int = 200
    test_rounds: int = 100
    purge_rounds: int = 2
    embargo_rounds: int = 2
    calibration_rounds: int = 50
    calibration_bins: int = 10
    calibration_shrinkage: int = 20

    def validate(self) -> None:
        if self.min_train_rounds <= 0 or self.test_rounds <= 0:
            raise ValueError("model train/test rounds must be positive")
        if self.purge_rounds < 0 or self.embargo_rounds < 0:
            raise ValueError("model purge/embargo rounds must be non-negative")
        if self.calibration_rounds < 2:
            raise ValueError("calibration_rounds must be at least 2")
        if self.calibration_bins <= 0 or self.calibration_shrinkage < 0:
            raise ValueError("invalid calibration settings")


@dataclass(frozen=True, slots=True)
class LegacySupportingCampaignConfig:
    features: LegacyFeatureConfig
    model: LegacyModelConfig
    pool: AbsolutePoolProjectionConfig
    economics: LegacyEconomicBenchmarkConfig

    def validate(self) -> None:
        self.features.validate()
        self.model.validate()
        self.pool.validate()
        self.economics.validate()
        if self.features.feature_lead_seconds != self.economics.decision_lead_seconds:
            raise ValueError("feature lead and economic decision lead must match exactly")
        purge_values = {
            self.model.purge_rounds,
            self.pool.purge_rounds,
            self.economics.purge_rounds,
        }
        if len(purge_values) != 1:
            raise ValueError("model, pool, and economic purge_rounds must match exactly")


@dataclass(frozen=True, slots=True)
class LegacySupportingCampaignManifest:
    schema_version: int
    source_class: str
    authoritative: bool
    legacy_source: dict[str, object]
    feature_config: LegacyFeatureConfig
    model_config: LegacyModelConfig
    pool_config: AbsolutePoolProjectionConfig
    feature_summary: dict[str, object]
    spot_sources: tuple[BinanceSourceSlice, ...]
    perp_sources: tuple[BinanceSourceSlice, ...]

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_class": self.source_class,
            "authoritative": self.authoritative,
            "legacy_source": self.legacy_source,
            "feature_config": asdict(self.feature_config),
            "model_config": asdict(self.model_config),
            "pool_config": asdict(self.pool_config),
            "feature_summary": self.feature_summary,
            "spot_sources": [item.as_dict() for item in self.spot_sources],
            "perp_sources": [item.as_dict() for item in self.perp_sources],
        }

    @property
    def digest(self) -> str:
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
        payload["campaign_digest"] = self.digest
        return payload


@dataclass(frozen=True, slots=True)
class LegacySupportingCampaignReport:
    authoritative: bool
    manifest: LegacySupportingCampaignManifest
    probability_metrics: OosMetrics
    direction_signal_count: int
    pool_projection_count: int
    economic_config: LegacyEconomicBenchmarkConfig
    economic_summary: dict[str, object]

    def payload(self) -> dict[str, object]:
        return {
            "authoritative": self.authoritative,
            "campaign_digest": self.manifest.digest,
            "probability_metrics": self.probability_metrics.as_dict(),
            "direction_signal_count": self.direction_signal_count,
            "pool_projection_count": self.pool_projection_count,
            "economic_config": asdict(self.economic_config),
            "economic_summary": self.economic_summary,
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
        payload["manifest"] = self.manifest.as_dict()
        return payload


def _validate_legacy_source(
    audit: LegacyRoundAuditReport,
    rounds: tuple[LegacyRoundRecord, ...],
) -> None:
    if audit.authoritative:
        raise ValueError("legacy supporting source must never be marked authoritative")
    if audit.source_class != LEGACY_ROUNDS_SOURCE_CLASS:
        raise ValueError("unexpected legacy source class")
    if not audit.structurally_ready or not audit.expected_epoch_envelope_ready:
        raise ValueError("legacy source failed structural/envelope audit")
    if not rounds:
        raise ValueError("legacy campaign requires rounds")
    if len({record.epoch for record in rounds}) != len(rounds):
        raise ValueError("legacy campaign selection contains duplicate epochs")
    if audit.first_epoch is None or audit.last_epoch is None:
        raise ValueError("legacy audit is missing its full epoch envelope")
    if rounds[0].epoch < audit.first_epoch or rounds[-1].epoch > audit.last_epoch:
        raise ValueError("legacy campaign selection lies outside audited source envelope")


def _legacy_source_payload(
    audit: LegacyRoundAuditReport,
    rounds: tuple[LegacyRoundRecord, ...],
) -> dict[str, object]:
    return {
        "audit": audit.as_dict(),
        "selection": {
            "row_count": len(rounds),
            "first_epoch": rounds[0].epoch,
            "last_epoch": rounds[-1].epoch,
            "first_start_timestamp": rounds[0].start_timestamp,
            "last_close_timestamp": rounds[-1].close_timestamp,
        },
    }


def _source_slices(
    source: ClickHouseParameterizedJsonSource,
    feature_result: LegacyFeatureBuildResult,
    feature_config: LegacyFeatureConfig,
) -> tuple[tuple[BinanceSourceSlice, ...], tuple[BinanceSourceSlice, ...]]:
    spot: tuple[BinanceSourceSlice, ...] = ()
    perp: tuple[BinanceSourceSlice, ...] = ()
    if feature_result.spot_query_start_ms is not None and feature_result.query_end_ms is not None:
        spot = load_binance_source_slices(
            source,
            market="BNBUSD",
            venue="spot",
            timestamp_unit=feature_config.spot_timestamp_unit,
            availability_lag_ms=feature_config.spot_availability_lag_ms,
            start_timestamp_ms=feature_result.spot_query_start_ms,
            end_timestamp_ms=feature_result.query_end_ms,
        )
    if (
        feature_config.include_perp
        and feature_result.perp_query_start_ms is not None
        and feature_result.query_end_ms is not None
    ):
        perp = load_binance_source_slices(
            source,
            market="BNBUSD",
            venue="um_futures",
            timestamp_unit=feature_config.perp_timestamp_unit,
            availability_lag_ms=feature_config.perp_availability_lag_ms,
            start_timestamp_ms=feature_result.perp_query_start_ms,
            end_timestamp_ms=feature_result.query_end_ms,
        )
    if feature_result.rows and not spot:
        raise ValueError("legacy campaign has feature rows but no bound Spot source slices")
    if feature_result.rows and feature_config.include_perp and not perp:
        raise ValueError("legacy campaign requires Perp source slices when include_perp=true")
    return spot, perp


def run_legacy_supporting_campaign(
    rounds: tuple[LegacyRoundRecord, ...],
    audit: LegacyRoundAuditReport,
    source: ClickHouseParameterizedJsonSource,
    config: LegacySupportingCampaignConfig,
) -> LegacySupportingCampaignReport:
    config.validate()
    ordered = tuple(sorted(rounds, key=lambda record: record.epoch))
    _validate_legacy_source(audit, ordered)

    features = build_legacy_clickhouse_feature_rows(
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
    model = run_legacy_walkforward_model(
        ordered,
        features.rows,
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
    economics: LegacyEconomicBenchmarkReport = run_legacy_economic_benchmark(
        ordered,
        dict(model.signals),
        projections,
        config.economics,
    )
    spot_sources, perp_sources = _source_slices(source, features, config.features)
    manifest = LegacySupportingCampaignManifest(
        schema_version=1,
        source_class=LEGACY_ROUNDS_SOURCE_CLASS,
        authoritative=False,
        legacy_source=_legacy_source_payload(audit, ordered),
        feature_config=config.features,
        model_config=config.model,
        pool_config=config.pool,
        feature_summary=features.as_dict(),
        spot_sources=spot_sources,
        perp_sources=perp_sources,
    )
    return LegacySupportingCampaignReport(
        authoritative=False,
        manifest=manifest,
        probability_metrics=model.metrics,
        direction_signal_count=len(model.signals),
        pool_projection_count=len(projections),
        economic_config=config.economics,
        economic_summary=economics.summary(),
    )
