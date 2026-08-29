from __future__ import annotations

from dataclasses import replace

import pytest

from pancake_prediction.contracts import MARKETS
from pancake_prediction.shadow_campaign import ShadowCampaignPolicy
from pancake_prediction.shadow_inference import ShadowInferenceConfig
from pancake_prediction.shadow_manifest import ShadowCampaignManifest
from pancake_prediction.shadow_runtime import (
    ShadowRuntimeConfig,
    build_shadow_runtime_campaign_manifest,
)


def _config() -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        chain_confirmations=3,
        spot_timestamp_unit="auto",
        spot_availability_lag_ms=250,
        perp_timestamp_unit="milliseconds",
        perp_availability_lag_ms=250,
        include_perp=True,
        flow_lookback_ms=60_000,
        max_spot_age_ms=5_000,
        max_perp_age_ms=5_000,
        max_chainlink_age_ms=300_000,
        chainlink_availability_lag_ms=1_000,
        oracle_history_updates=512,
        oracle_hazard_horizon_ms=5_000,
        oracle_hazard_min_intervals=8,
        inference=ShadowInferenceConfig(
            min_train_rounds=300,
            calibration_rounds=60,
            purge_rounds=2,
            pool_min_train_rounds=150,
            pool_window_rounds=400,
            stake_wei=10**16,
            bet_gas_wei=5 * 10**13,
            claim_gas_wei=3 * 10**13,
            inclusion_latency_seconds=2,
            min_expected_value_wei=0,
            decision_lead_seconds=20,
        ),
        campaign_policy=ShadowCampaignPolicy(),
    )


def _manifest(
    config: ShadowRuntimeConfig | None = None,
    *,
    proxy: str = "0x" + "11" * 20,
    aggregator: str = "0x" + "22" * 20,
) -> ShadowCampaignManifest:
    return build_shadow_runtime_campaign_manifest(
        MARKETS["BNBUSD"],
        oracle_proxy_anchor=proxy,
        chainlink_aggregator_anchor=aggregator,
        config=_config() if config is None else config,
    )


def test_shadow_campaign_manifest_is_deterministic_and_normalizes_addresses() -> None:
    first = _manifest()
    second = _manifest(
        proxy="0x" + "11" * 20,
        aggregator="0x" + "22" * 20,
    )

    assert first.canonical_payload() == second.canonical_payload()
    assert first.digest == second.digest
    assert len(first.digest) == 64
    payload = first.canonical_payload()
    assert payload["prediction_contract"] == MARKETS["BNBUSD"].address.lower()
    assert payload["oracle_proxy_anchor"] == "0x" + "11" * 20
    assert payload["chainlink_aggregator_anchor"] == "0x" + "22" * 20


@pytest.mark.parametrize(
    "changed",
    (
        "stake",
        "spot_lineage",
        "latency",
        "campaign_policy",
        "chain_confirmations",
    ),
)
def test_shadow_campaign_manifest_semantic_drift_changes_digest(changed: str) -> None:
    base = _config()
    if changed == "stake":
        config = replace(
            base,
            inference=replace(base.inference, stake_wei=base.inference.stake_wei + 1),
        )
    elif changed == "spot_lineage":
        config = replace(
            base,
            spot_availability_lag_ms=base.spot_availability_lag_ms + 1,
        )
    elif changed == "latency":
        config = replace(
            base,
            inference=replace(
                base.inference,
                inclusion_latency_seconds=base.inference.inclusion_latency_seconds + 1,
            ),
        )
    elif changed == "campaign_policy":
        config = replace(
            base,
            campaign_policy=replace(
                base.campaign_policy,
                min_predictions=base.campaign_policy.min_predictions + 1,
            ),
        )
    elif changed == "chain_confirmations":
        config = replace(base, chain_confirmations=base.chain_confirmations + 1)
    else:
        raise AssertionError(changed)

    assert _manifest(config).digest != _manifest(base).digest


def test_shadow_campaign_manifest_anchor_drift_changes_digest() -> None:
    base = _manifest()
    changed_proxy = _manifest(proxy="0x" + "33" * 20)
    changed_aggregator = _manifest(aggregator="0x" + "44" * 20)

    assert changed_proxy.digest != base.digest
    assert changed_aggregator.digest != base.digest


def test_shadow_campaign_manifest_ignores_performance_only_tuning() -> None:
    base = _config()
    tuned = replace(
        base,
        chain_chunk_size=17,
        chain_reorg_lookback=128,
        binance_bootstrap_window_ms=300_000,
        binance_batch_size=777,
        binance_max_pages=7,
        dataset_chunk_span_ms=900_000,
    )

    assert _manifest(tuned).digest == _manifest(base).digest


def test_shadow_campaign_manifest_ignores_disabled_perp_lineage_values() -> None:
    base = replace(_config(), include_perp=False)
    changed = replace(
        base,
        perp_timestamp_unit="microseconds",
        perp_availability_lag_ms=999,
    )

    assert _manifest(changed).digest == _manifest(base).digest


def test_shadow_campaign_manifest_rejects_secret_keys() -> None:
    manifest = ShadowCampaignManifest(
        chain_id=56,
        market="BNBUSD",
        prediction_contract=MARKETS["BNBUSD"].address,
        oracle_proxy_anchor="0x" + "11" * 20,
        chainlink_aggregator_anchor="0x" + "22" * 20,
        semantic_config={"password": "must-not-be-serialized"},
    )

    with pytest.raises(ValueError, match="not allowed"):
        manifest.canonical_payload()
