from __future__ import annotations

import json
from pathlib import Path

import pancake_prediction.campaign_sensitivity as campaign_sensitivity
from pancake_prediction.campaign_evaluation import EconomicCampaignConfig


SCENARIO_PATH = Path("config/recent-economic-sensitivity-aug18.json")
EXPECTED_NAMES = {
    "baseline",
    "optimistic-low-cost",
    "low-stake",
    "high-stake",
    "high-gas",
    "slow-inclusion",
    "strict-ev",
    "combined-stress",
}


def _base_config() -> EconomicCampaignConfig:
    return EconomicCampaignConfig(
        stake_wei=1,
        bet_gas_wei=0,
        claim_gas_wei=0,
        inclusion_latency_seconds=0,
        min_expected_value_wei=0,
        decision_lead_seconds=20,
        initial_interval_seconds=300,
        initial_treasury_fee_bps=300,
        initial_buffer_seconds=30,
        min_train_rounds=100,
        test_rounds=50,
        purge_rounds=2,
        embargo_rounds=2,
        calibration_rounds=20,
        pool_min_train_rounds=50,
        pool_window_rounds=200,
        run_ablation=False,
    )


def _load_scenarios() -> tuple[campaign_sensitivity.EconomicSensitivityScenario, ...]:
    payload = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    return campaign_sensitivity.parse_sensitivity_scenarios(
        payload,
        base_config=_base_config(),
    )


def test_aug18_robustness_scenarios_are_parseable_and_exact() -> None:
    scenarios = _load_scenarios()

    assert len(scenarios) == 8
    assert {scenario.name for scenario in scenarios} == EXPECTED_NAMES
    assert all(scenario.config.run_ablation is False for scenario in scenarios)


def test_aug18_robustness_baseline_matches_economic_smoke() -> None:
    scenarios = _load_scenarios()
    by_name = {scenario.name: scenario.config for scenario in scenarios}
    baseline = by_name["baseline"]

    assert baseline.stake_wei == 10_000_000_000_000_000
    assert baseline.bet_gas_wei == 50_000_000_000_000
    assert baseline.claim_gas_wei == 30_000_000_000_000
    assert baseline.inclusion_latency_seconds == 2
    assert baseline.min_expected_value_wei == 0


def test_aug18_combined_stress_is_stricter_than_baseline() -> None:
    scenarios = _load_scenarios()
    by_name = {scenario.name: scenario.config for scenario in scenarios}
    baseline = by_name["baseline"]
    stress = by_name["combined-stress"]

    assert stress.stake_wei < baseline.stake_wei
    assert stress.bet_gas_wei > baseline.bet_gas_wei
    assert stress.claim_gas_wei > baseline.claim_gas_wei
    assert stress.inclusion_latency_seconds > baseline.inclusion_latency_seconds
    assert stress.min_expected_value_wei > baseline.min_expected_value_wei
