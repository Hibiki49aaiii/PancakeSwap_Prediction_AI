from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction.binance_archive import BinanceArchiveProvenance
from pancake_prediction.oracle_history import ActiveOracleHistory, OracleActivation, OracleAnchor
from pancake_prediction.replay import ReplaySnapshot
from pancake_prediction.research_inputs import CanonicalResearchInputs
from pancake_prediction.research_manifest import (
    ResearchTimingAssumptions,
    build_research_campaign_manifest,
)


def _inputs() -> CanonicalResearchInputs:
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, ())
    oracle = ActiveOracleHistory(
        market="BNBUSD",
        anchor=OracleAnchor(100, "0x" + "11" * 20),
        activations=(
            OracleActivation(101, -1, -1, "0x" + "11" * 20, "historical-anchor"),
        ),
        events=(),
        canonical_answer_updates=0,
        excluded_inactive_oracle=0,
        excluded_unanchored=0,
    )
    return CanonicalResearchInputs(
        market="BNBUSD",
        replay=replay,
        events=(),
        oracle_history=oracle,
        prediction_event_count=0,
    )


def _archive(*, digest: str = "b" * 64, symbol: str = "BNBUSDT") -> BinanceArchiveProvenance:
    return BinanceArchiveProvenance(
        schema_version=1,
        source_sha256=digest,
        source_name="BNBUSDT-aggTrades-2026-08.zip",
        venue="spot",
        symbol=symbol,
        timestamp_unit="auto",
        availability_lag_ms=25,
        row_count=100,
        first_trade_timestamp_ms=1_000,
        last_trade_timestamp_ms=2_000,
        first_aggregate_trade_id=1,
        last_aggregate_trade_id=100,
    )


def test_manifest_digest_is_deterministic_and_binds_latency_assumptions() -> None:
    inputs = _inputs()
    source = _archive()
    first = build_research_campaign_manifest(inputs, spot_archives=(source,))
    second = build_research_campaign_manifest(inputs, spot_archives=(source,))
    slower = build_research_campaign_manifest(
        inputs,
        spot_archives=(source,),
        timing=ResearchTimingAssumptions(chainlink_availability_lag_ms=500),
    )

    assert first.digest == second.digest
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.digest != slower.digest


def test_manifest_rejects_cross_market_archive() -> None:
    with pytest.raises(ValueError, match="archive symbol mismatch"):
        build_research_campaign_manifest(
            _inputs(),
            spot_archives=(_archive(symbol="BTCUSDT"),),
        )


def test_manifest_rejects_duplicate_archive_content() -> None:
    source = _archive()
    duplicate = replace(source, source_name="duplicate.zip")
    with pytest.raises(ValueError, match="duplicate Binance archive source hash"):
        build_research_campaign_manifest(
            _inputs(),
            spot_archives=(source, duplicate),
        )
