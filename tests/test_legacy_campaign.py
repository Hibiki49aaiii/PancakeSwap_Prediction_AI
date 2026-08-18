from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace

import pytest

from pancake_prediction.absolute_pool_projection import AbsolutePoolProjectionConfig
from pancake_prediction.binance import AggTrade
from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.legacy_benchmark import LegacyEconomicBenchmarkConfig
from pancake_prediction.legacy_campaign import (
    LegacyFeatureConfig,
    LegacyModelConfig,
    LegacySupportingCampaignConfig,
    run_legacy_supporting_campaign,
)
from pancake_prediction.legacy_rounds import (
    LEGACY_ROUNDS_SOURCE_BLOB_SHA,
    LEGACY_ROUNDS_SOURCE_CLASS,
    LEGACY_ROUNDS_SOURCE_COMMIT,
    LEGACY_ROUNDS_SOURCE_PATH,
    LEGACY_ROUNDS_SOURCE_REPOSITORY,
    LegacyRoundAuditReport,
    LegacyRoundRecord,
)


class CampaignSource:
    def __init__(self, spot: tuple[AggTrade, ...], perp: tuple[AggTrade, ...]) -> None:
        self.trades = {"spot": spot, "um_futures": perp}
        self.calls: list[dict[str, QueryParameter]] = []

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        if parameters is None:
            raise AssertionError("campaign source queries must be parameterized")
        values = dict(parameters)
        self.calls.append(values)
        venue = str(values["venue"])
        start = int(values["start_timestamp_ms"])
        end = int(values["end_timestamp_ms"])
        selected = tuple(
            trade
            for trade in self.trades[venue]
            if start <= trade.trade_timestamp_ms < end
        )
        if "GROUP BY source_sha256,source_name" in query:
            if not selected:
                return
            yield {
                "source_sha256": ("a" if venue == "spot" else "b") * 64,
                "source_name": f"{venue}-fixture.zip",
                "row_count": len(selected),
                "first_trade_timestamp_ms": selected[0].trade_timestamp_ms,
                "last_trade_timestamp_ms": selected[-1].trade_timestamp_ms,
                "first_aggregate_trade_id": selected[0].aggregate_trade_id,
                "last_aggregate_trade_id": selected[-1].aggregate_trade_id,
            }
            return
        if "FROM binance_agg_trades FINAL" not in query:
            raise AssertionError("unexpected campaign query")
        for trade in selected:
            yield {
                "symbol": trade.symbol,
                "event_timestamp_ms": trade.event_timestamp_ms,
                "trade_timestamp_ms": trade.trade_timestamp_ms,
                "price_e8": trade.price_e8,
                "quantity_e8": trade.quantity_e8,
                "aggressive_side": trade.aggressive_side,
                "aggregate_trade_id": trade.aggregate_trade_id,
            }


def _round(epoch: int) -> LegacyRoundRecord:
    start = 1_000 + epoch * 300
    lock = start + 300
    close = lock + 300
    bull = epoch % 2 == 1
    lock_price = 30_000_000_000
    return LegacyRoundRecord(
        epoch=epoch,
        start_timestamp=start,
        lock_timestamp=lock,
        close_timestamp=close,
        lock_price_e8=lock_price,
        close_price_e8=lock_price + (100_000_000 if bull else -100_000_000),
        lock_oracle_id=10_000 + epoch,
        close_oracle_id=20_000 + epoch,
        total_amount_wei=2 * 10**18,
        bull_amount_wei=(12 if bull else 8) * 10**17,
        bear_amount_wei=(8 if bull else 12) * 10**17,
        reward_base_cal_amount_wei=12 * 10**17,
        reward_amount_wei=194 * 10**16,
        oracle_called=True,
    )


def _trade(
    trade_id: int,
    timestamp_ms: int,
    *,
    bullish: bool,
    perp: bool,
) -> AggTrade:
    return AggTrade(
        symbol="BNBUSDT",
        event_timestamp_ms=timestamp_ms + (40 if perp else 25),
        trade_timestamp_ms=timestamp_ms,
        price_e8=30_000_000_000 + (2_000_000 if perp else 1_000_000),
        quantity_e8=100_000_000,
        aggressive_side="buy" if bullish else "sell",
        aggregate_trade_id=trade_id,
    )


def _fixture() -> tuple[
    tuple[LegacyRoundRecord, ...],
    LegacyRoundAuditReport,
    CampaignSource,
]:
    rounds = tuple(_round(epoch) for epoch in range(100, 140))
    spot: list[AggTrade] = []
    perp: list[AggTrade] = []
    for index, record in enumerate(rounds):
        timestamp_ms = (record.lock_timestamp - 21) * 1_000
        bullish = record.label == "bull"
        spot.append(_trade(1_000 + index, timestamp_ms, bullish=bullish, perp=False))
        perp.append(_trade(2_000 + index, timestamp_ms, bullish=bullish, perp=True))
    audit = LegacyRoundAuditReport(
        source_class=LEGACY_ROUNDS_SOURCE_CLASS,
        authoritative=False,
        source_repository=LEGACY_ROUNDS_SOURCE_REPOSITORY,
        source_commit=LEGACY_ROUNDS_SOURCE_COMMIT,
        source_blob_sha=LEGACY_ROUNDS_SOURCE_BLOB_SHA,
        source_path=LEGACY_ROUNDS_SOURCE_PATH,
        source_sha256="c" * 64,
        row_count=len(rounds),
        first_epoch=rounds[0].epoch,
        last_epoch=rounds[-1].epoch,
        first_start_timestamp=rounds[0].start_timestamp,
        last_close_timestamp=rounds[-1].close_timestamp,
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
    return rounds, audit, CampaignSource(tuple(spot), tuple(perp))


def _config() -> LegacySupportingCampaignConfig:
    return LegacySupportingCampaignConfig(
        features=LegacyFeatureConfig(
            spot_timestamp_unit="milliseconds",
            spot_availability_lag_ms=25,
            perp_timestamp_unit="milliseconds",
            perp_availability_lag_ms=40,
            include_perp=True,
            chunk_span_ms=20_000_000,
            feature_lead_seconds=20,
            max_spot_age_ms=5_000,
            max_perp_age_ms=5_000,
        ),
        model=LegacyModelConfig(
            min_train_rounds=12,
            test_rounds=6,
            purge_rounds=2,
            embargo_rounds=1,
            calibration_rounds=4,
            calibration_bins=4,
            calibration_shrinkage=4,
        ),
        pool=AbsolutePoolProjectionConfig(
            min_train_rounds=5,
            window_rounds=20,
            purge_rounds=2,
        ),
        economics=LegacyEconomicBenchmarkConfig(
            stake_wei=10**15,
            bet_gas_wei=10**13,
            claim_gas_wei=5 * 10**12,
            inclusion_latency_seconds=3,
            treasury_fee_bps=300,
            decision_lead_seconds=20,
            min_expected_value_wei=0,
            purge_rounds=2,
        ),
    )


def test_legacy_supporting_campaign_binds_sources_and_never_becomes_authoritative() -> None:
    rounds, audit, source = _fixture()
    report = run_legacy_supporting_campaign(rounds, audit, source, _config())
    payload = report.as_dict()

    assert report.authoritative is False
    assert report.manifest.authoritative is False
    assert report.manifest.source_class == "third_party_historical_benchmark"
    assert report.direction_signal_count > 0
    assert report.pool_projection_count > 0
    assert report.manifest.spot_sources[0].source_sha256 == "a" * 64
    assert report.manifest.perp_sources[0].source_sha256 == "b" * 64
    assert len(report.manifest.digest) == 64
    assert len(report.evaluation_digest) == 64
    assert payload["economic_summary"]["authoritative"] is False


def test_economic_cost_changes_evaluation_digest_not_source_campaign_digest() -> None:
    rounds, audit, source = _fixture()
    report = run_legacy_supporting_campaign(rounds, audit, source, _config())
    changed_economics = replace(
        report.economic_config,
        bet_gas_wei=report.economic_config.bet_gas_wei + 1,
    )
    changed_report = replace(
        report,
        economic_config=changed_economics,
        economic_summary={
            **report.economic_summary,
            "config": {
                **report.economic_summary["config"],
                "bet_gas_wei": changed_economics.bet_gas_wei,
            },
        },
    )

    assert changed_report.manifest.digest == report.manifest.digest
    assert changed_report.evaluation_digest != report.evaluation_digest


def test_legacy_campaign_rejects_timing_and_purge_mismatch() -> None:
    config = _config()
    with pytest.raises(ValueError, match="feature lead"):
        replace(
            config,
            economics=replace(config.economics, decision_lead_seconds=19),
        ).validate()
    with pytest.raises(ValueError, match="purge_rounds"):
        replace(
            config,
            pool=replace(config.pool, purge_rounds=3),
        ).validate()


def test_legacy_campaign_rejects_authoritative_or_unready_source() -> None:
    rounds, audit, source = _fixture()
    with pytest.raises(ValueError, match="never be marked authoritative"):
        run_legacy_supporting_campaign(
            rounds,
            replace(audit, authoritative=True),
            source,
            _config(),
        )
    with pytest.raises(ValueError, match="structural/envelope"):
        run_legacy_supporting_campaign(
            rounds,
            replace(audit, structurally_ready=False),
            source,
            _config(),
        )
