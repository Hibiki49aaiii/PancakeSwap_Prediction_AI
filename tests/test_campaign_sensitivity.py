from __future__ import annotations

import pytest

from pancake_prediction.campaign_evaluation import EconomicCampaignConfig
from pancake_prediction.campaign_sensitivity import (
    EconomicSensitivityReport,
    EconomicSensitivityResult,
    EconomicSensitivityScenario,
    _validate_scenarios,
)


def _config(
    *,
    stake_wei: int = 10**15,
    bet_gas_wei: int = 10**13,
    claim_gas_wei: int = 5 * 10**12,
    latency: int = 3,
    min_ev: int = 0,
    purge_rounds: int = 2,
) -> EconomicCampaignConfig:
    return EconomicCampaignConfig(
        stake_wei=stake_wei,
        bet_gas_wei=bet_gas_wei,
        claim_gas_wei=claim_gas_wei,
        inclusion_latency_seconds=latency,
        min_expected_value_wei=min_ev,
        min_train_rounds=20,
        test_rounds=10,
        purge_rounds=purge_rounds,
        embargo_rounds=2,
        calibration_rounds=10,
        pool_min_train_rounds=10,
        pool_window_rounds=20,
        run_ablation=False,
    )


def test_sensitivity_allows_only_economic_dimensions_to_vary() -> None:
    scenarios = _validate_scenarios(
        (
            EconomicSensitivityScenario("base", _config()),
            EconomicSensitivityScenario(
                "stress",
                _config(
                    stake_wei=2 * 10**15,
                    bet_gas_wei=2 * 10**13,
                    claim_gas_wei=10**13,
                    latency=8,
                    min_ev=10**12,
                ),
            ),
        )
    )
    assert tuple(item.name for item in scenarios) == ("base", "stress")


def test_sensitivity_rejects_structural_model_change() -> None:
    with pytest.raises(ValueError, match="may vary only"):
        _validate_scenarios(
            (
                EconomicSensitivityScenario("base", _config()),
                EconomicSensitivityScenario(
                    "bad",
                    _config(purge_rounds=3),
                ),
            )
        )


def test_sensitivity_rejects_duplicate_names_and_single_scenario() -> None:
    with pytest.raises(ValueError, match="at least two"):
        _validate_scenarios((EconomicSensitivityScenario("only", _config()),))
    with pytest.raises(ValueError, match="duplicate"):
        _validate_scenarios(
            (
                EconomicSensitivityScenario("same", _config()),
                EconomicSensitivityScenario("same", _config(latency=5)),
            )
        )


def _result(name: str, pnl: int, roi: int, drawdown: int) -> EconomicSensitivityResult:
    return EconomicSensitivityResult(
        name=name,
        evaluation_digest=("a" if name == "a" else "b") * 64,
        config=_config(latency=3 if name == "a" else 8),
        probability_metrics={"brier_score": 0.24},
        backtest_summary={
            "pnl_wei": pnl,
            "roi_ppm": roi,
            "max_drawdown_wei": drawdown,
            "trade_count": 10,
        },
        direction_signal_count=100,
        pool_projection_count=95,
        joint_epoch_count=90,
    )


def test_sensitivity_report_exposes_worst_case_without_declaring_profit_gate() -> None:
    report = EconomicSensitivityReport(
        campaign_digest="c" * 64,
        scenarios=(
            _result("a", 100, 1_000, 25),
            _result("b", -50, -500, 80),
        ),
    )
    payload = report.as_dict()

    assert payload["scenario_count"] == 2
    assert payload["positive_pnl_scenarios"] == 1
    assert payload["all_scenarios_positive_pnl"] is False
    assert payload["min_pnl_wei"] == -50
    assert payload["max_pnl_wei"] == 100
    assert payload["min_roi_ppm"] == -500
    assert payload["max_drawdown_wei"] == 80
    assert isinstance(payload["sensitivity_digest"], str)
    assert len(str(payload["sensitivity_digest"])) == 64
