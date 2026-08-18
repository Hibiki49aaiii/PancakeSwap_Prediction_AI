from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from pancake_prediction.absolute_pool_projection import AbsolutePoolProjectionConfig
from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.legacy_benchmark import LegacyEconomicBenchmarkConfig
from pancake_prediction.legacy_campaign import (
    LegacyFeatureConfig,
    LegacyModelConfig,
    LegacySupportingCampaignConfig,
    LegacySupportingCampaignManifest,
    LegacySupportingCampaignReport,
)
from pancake_prediction.legacy_rounds import LegacyRoundAuditReport, LegacyRoundRecord
from pancake_prediction.legacy_sensitivity import run_legacy_supporting_sensitivity
from pancake_prediction.walkforward import OosMetrics


class DummySource:
    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        del query, parameters
        return iter(())


def _economics(*, bet_gas_wei: int) -> LegacyEconomicBenchmarkConfig:
    return LegacyEconomicBenchmarkConfig(
        stake_wei=1_000,
        bet_gas_wei=bet_gas_wei,
        claim_gas_wei=10,
        inclusion_latency_seconds=3,
        decision_lead_seconds=20,
        purge_rounds=2,
    )


def _base_config() -> LegacySupportingCampaignConfig:
    return LegacySupportingCampaignConfig(
        features=LegacyFeatureConfig(
            spot_timestamp_unit="milliseconds",
            spot_availability_lag_ms=250,
            feature_lead_seconds=20,
        ),
        model=LegacyModelConfig(
            min_train_rounds=10,
            test_rounds=5,
            purge_rounds=2,
            calibration_rounds=4,
        ),
        pool=AbsolutePoolProjectionConfig(
            min_train_rounds=5,
            window_rounds=10,
            purge_rounds=2,
        ),
        economics=_economics(bet_gas_wei=20),
    )


def _audit() -> LegacyRoundAuditReport:
    return LegacyRoundAuditReport(
        source_class="third_party_historical_benchmark",
        authoritative=False,
        source_repository="fixture",
        source_commit="a" * 40,
        source_blob_sha="b" * 40,
        source_path="rounds.csv.gz",
        source_sha256="c" * 64,
        row_count=2,
        first_epoch=1,
        last_epoch=2,
        first_start_timestamp=100,
        last_close_timestamp=900,
        duplicate_epochs=0,
        epoch_gaps=0,
        non_monotonic_timestamps=0,
        pool_sum_mismatches=0,
        reward_base_mismatches=0,
        reward_amount_exceeds_pool=0,
        refunded_rounds=0,
        tie_rounds=0,
        empty_bull_pool_rounds=0,
        empty_bear_pool_rounds=0,
        expected_epoch_envelope_ready=True,
        structurally_ready=True,
        amount_precision_note="fixture",
    )


def _rounds() -> tuple[LegacyRoundRecord, ...]:
    return (
        LegacyRoundRecord(
            epoch=1,
            start_timestamp=100,
            lock_timestamp=400,
            close_timestamp=700,
            lock_price_e8=100,
            close_price_e8=101,
            lock_oracle_id=1,
            close_oracle_id=2,
            total_amount_wei=2_000,
            bull_amount_wei=1_000,
            bear_amount_wei=1_000,
            reward_base_cal_amount_wei=1_000,
            reward_amount_wei=1_940,
            oracle_called=True,
        ),
        LegacyRoundRecord(
            epoch=2,
            start_timestamp=400,
            lock_timestamp=700,
            close_timestamp=900,
            lock_price_e8=100,
            close_price_e8=99,
            lock_oracle_id=3,
            close_oracle_id=4,
            total_amount_wei=2_000,
            bull_amount_wei=1_000,
            bear_amount_wei=1_000,
            reward_base_cal_amount_wei=1_000,
            reward_amount_wei=1_940,
            oracle_called=True,
        ),
    )


def _manifest(base: LegacySupportingCampaignConfig) -> LegacySupportingCampaignManifest:
    return LegacySupportingCampaignManifest(
        schema_version=1,
        source_class="third_party_historical_benchmark",
        authoritative=False,
        legacy_source={"source_sha256": "c" * 64},
        feature_config=base.features,
        model_config=base.model,
        pool_config=base.pool,
        feature_summary={"research_feature_rows": 2},
        spot_sources=(),
        perp_sources=(),
    )


def test_legacy_sensitivity_keeps_source_digest_and_varies_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base_config()
    manifest = _manifest(base)
    metrics = OosMetrics(
        market="BNBUSD",
        n_scored=2,
        n_ties_excluded=0,
        n_missing_signal=0,
        bull_base_rate=0.5,
        brier_score=0.24,
        brier_skill_score=0.04,
        log_loss=0.68,
        ece_10=0.1,
        accuracy=0.5,
        accuracy_ci95=(0.1, 0.9),
    )

    def fake_runner(
        rounds: tuple[LegacyRoundRecord, ...],
        audit: LegacyRoundAuditReport,
        source: DummySource,
        config: LegacySupportingCampaignConfig,
    ) -> LegacySupportingCampaignReport:
        del rounds, audit, source
        pnl = 100 - config.economics.bet_gas_wei
        return LegacySupportingCampaignReport(
            authoritative=False,
            manifest=manifest,
            probability_metrics=metrics,
            direction_signal_count=2,
            pool_projection_count=2,
            economic_config=config.economics,
            economic_summary={
                "authoritative": False,
                "pnl_wei": pnl,
                "roi_ppm": pnl * 1_000,
                "max_drawdown_wei": config.economics.bet_gas_wei,
            },
        )

    monkeypatch.setattr(
        "pancake_prediction.legacy_sensitivity.run_legacy_supporting_campaign",
        fake_runner,
    )
    report = run_legacy_supporting_sensitivity(
        _rounds(),
        _audit(),
        DummySource(),
        base,
        {
            "base": _economics(bet_gas_wei=20),
            "stress": _economics(bet_gas_wei=120),
        },
    )

    assert report.authoritative is False
    assert report.campaign_digest == manifest.digest
    assert len({item.evaluation_digest for item in report.scenarios}) == 2
    assert report.positive_pnl_scenarios == 1
    assert report.min_pnl_wei == -20
    assert report.min_roi_ppm == -20_000
    assert report.max_drawdown_wei == 120
    assert report.as_dict()["profitability_gate_eligible"] is False


def test_legacy_sensitivity_rejects_single_or_structurally_mismatched_scenario() -> None:
    base = _base_config()
    with pytest.raises(ValueError, match="at least two"):
        run_legacy_supporting_sensitivity(
            _rounds(),
            _audit(),
            DummySource(),
            base,
            {"only": _economics(bet_gas_wei=20)},
        )

    bad = LegacyEconomicBenchmarkConfig(
        stake_wei=1_000,
        bet_gas_wei=20,
        claim_gas_wei=10,
        inclusion_latency_seconds=3,
        decision_lead_seconds=19,
        purge_rounds=2,
    )
    with pytest.raises(ValueError, match="feature lead"):
        run_legacy_supporting_sensitivity(
            _rounds(),
            _audit(),
            DummySource(),
            base,
            {
                "base": _economics(bet_gas_wei=20),
                "bad": bad,
            },
        )
