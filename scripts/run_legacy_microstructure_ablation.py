from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pancake_prediction.absolute_pool_projection import AbsolutePoolProjectionConfig
from pancake_prediction.clickhouse import ClickHouseHttpClient
from pancake_prediction.clickhouse_schema import inspect_binance_trade_schema
from pancake_prediction.legacy_benchmark import LegacyEconomicBenchmarkConfig
from pancake_prediction.legacy_campaign import (
    LegacyFeatureConfig,
    LegacyModelConfig,
    LegacySupportingCampaignConfig,
    run_legacy_supporting_campaign,
)
from pancake_prediction.legacy_features import build_legacy_clickhouse_feature_rows
from pancake_prediction.legacy_microstructure import enrich_legacy_microstructure_rows
from pancake_prediction.legacy_microstructure_ablation import (
    run_legacy_microstructure_ablation,
)
from pancake_prediction.legacy_pool_projection import build_legacy_absolute_pool_projections
from pancake_prediction.legacy_rounds import audit_legacy_rounds, load_legacy_rounds


def _client() -> ClickHouseHttpClient:
    endpoint = os.environ.get("CLICKHOUSE_URL", "").strip()
    if not endpoint:
        raise SystemExit("CLICKHOUSE_URL is required")
    return ClickHouseHttpClient(
        endpoint,
        database=os.environ.get("CLICKHOUSE_DATABASE", "default"),
        username=os.environ.get("CLICKHOUSE_USER"),
        password=os.environ.get("CLICKHOUSE_PASSWORD"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-timestamp", type=int, required=True)
    parser.add_argument("--end-timestamp", type=int, required=True)
    parser.add_argument("--spot-availability-lag-ms", type=int, required=True)
    parser.add_argument("--perp-availability-lag-ms", type=int, required=True)
    parser.add_argument("--feature-lead-seconds", type=int, required=True)
    parser.add_argument("--stake-wei", type=int, required=True)
    parser.add_argument("--bet-gas-wei", type=int, required=True)
    parser.add_argument("--claim-gas-wei", type=int, required=True)
    parser.add_argument("--inclusion-latency-seconds", type=int, required=True)
    parser.add_argument("--min-expected-value-wei", type=int, default=0)
    parser.add_argument("--min-train-rounds", type=int, default=500)
    parser.add_argument("--test-rounds", type=int, default=250)
    parser.add_argument("--purge-rounds", type=int, default=2)
    parser.add_argument("--embargo-rounds", type=int, default=2)
    parser.add_argument("--calibration-rounds", type=int, default=100)
    parser.add_argument("--pool-min-train-rounds", type=int, default=100)
    parser.add_argument("--pool-window-rounds", type=int, default=1_000)
    parser.add_argument("--chunk-span-ms", type=int, default=3_600_000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.end_timestamp <= args.start_timestamp:
        raise SystemExit("end timestamp must be after start timestamp")

    client = _client()
    if not inspect_binance_trade_schema(client).ready:
        raise SystemExit("ClickHouse binance_agg_trades schema is not ready")

    audit = audit_legacy_rounds(args.legacy_archive)
    selected = tuple(
        record
        for record in load_legacy_rounds(args.legacy_archive)
        if record.start_timestamp >= args.start_timestamp
        and record.close_timestamp < args.end_timestamp
    )
    if not selected:
        raise SystemExit("legacy selection contains no complete rounds")

    features = LegacyFeatureConfig(
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=args.spot_availability_lag_ms,
        perp_timestamp_unit="milliseconds",
        perp_availability_lag_ms=args.perp_availability_lag_ms,
        include_perp=True,
        chunk_span_ms=args.chunk_span_ms,
        feature_lead_seconds=args.feature_lead_seconds,
        flow_lookback_ms=60_000,
    )
    model = LegacyModelConfig(
        min_train_rounds=args.min_train_rounds,
        test_rounds=args.test_rounds,
        purge_rounds=args.purge_rounds,
        embargo_rounds=args.embargo_rounds,
        calibration_rounds=args.calibration_rounds,
    )
    pool = AbsolutePoolProjectionConfig(
        min_train_rounds=args.pool_min_train_rounds,
        window_rounds=args.pool_window_rounds,
        purge_rounds=args.purge_rounds,
    )
    economics = LegacyEconomicBenchmarkConfig(
        stake_wei=args.stake_wei,
        bet_gas_wei=args.bet_gas_wei,
        claim_gas_wei=args.claim_gas_wei,
        inclusion_latency_seconds=args.inclusion_latency_seconds,
        treasury_fee_bps=300,
        decision_lead_seconds=args.feature_lead_seconds,
        min_expected_value_wei=args.min_expected_value_wei,
        purge_rounds=args.purge_rounds,
    )
    config = LegacySupportingCampaignConfig(
        features=features,
        model=model,
        pool=pool,
        economics=economics,
    )
    baseline = run_legacy_supporting_campaign(selected, audit, client, config)
    base_rows = build_legacy_clickhouse_feature_rows(
        selected,
        client,
        spot_timestamp_unit=features.spot_timestamp_unit,
        spot_availability_lag_ms=features.spot_availability_lag_ms,
        perp_timestamp_unit=features.perp_timestamp_unit,
        perp_availability_lag_ms=features.perp_availability_lag_ms,
        include_perp=features.include_perp,
        chunk_span_ms=features.chunk_span_ms,
        feature_lead_seconds=features.feature_lead_seconds,
        flow_lookback_ms=features.flow_lookback_ms,
        max_spot_age_ms=features.max_spot_age_ms,
        max_perp_age_ms=features.max_perp_age_ms,
    )
    enriched = enrich_legacy_microstructure_rows(
        base_rows.rows,
        client,
        spot_timestamp_unit=features.spot_timestamp_unit,
        spot_availability_lag_ms=features.spot_availability_lag_ms,
        perp_timestamp_unit=features.perp_timestamp_unit,
        perp_availability_lag_ms=features.perp_availability_lag_ms,
        include_perp=features.include_perp,
        chunk_span_ms=features.chunk_span_ms,
    )
    projections = build_legacy_absolute_pool_projections(
        selected,
        decision_lead_seconds=features.feature_lead_seconds,
        config=pool,
    )
    report = run_legacy_microstructure_ablation(
        selected,
        enriched.rows,
        projections,
        economics,
        baseline_probability=baseline.probability_metrics.as_dict(),
        baseline_economics=baseline.economic_summary,
        min_train_rounds=model.min_train_rounds,
        test_rounds=model.test_rounds,
        purge_rounds=model.purge_rounds,
        embargo_rounds=model.embargo_rounds,
        calibration_rounds=model.calibration_rounds,
        calibration_bins=model.calibration_bins,
        calibration_shrinkage=model.calibration_shrinkage,
    )
    payload = report.as_dict()
    payload["baseline_campaign_digest"] = baseline.manifest.digest
    payload["baseline_evaluation_digest"] = baseline.evaluation_digest
    payload["selection"] = {
        "start_timestamp": args.start_timestamp,
        "end_timestamp": args.end_timestamp,
        "complete_rounds": len(selected),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
