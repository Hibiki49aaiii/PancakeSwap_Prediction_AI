from __future__ import annotations

import pytest

from pancake_prediction.campaign_evaluation import EconomicCampaignConfig
from pancake_prediction.campaign_sensitivity import (
    EconomicSensitivityReport,
    EconomicSensitivityResult,
    EconomicSensitivityScenario,
    _validate_scenarios,
    parse_sensitivity_scenarios,
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


def _scenario_payload() -> dict[str, object]:
    return {
        "scenarios": [
            {
                "name": "base",
                "stake_wei": 10**15,
                "bet_gas_wei": 10**13,
                "claim_gas_wei": 5 * 10**12,
                "inclusion_latency_seconds": 3,
            },
            {
                "name": "stress",
                "stake_wei": 2 * 10**15,
                "bet_gas_wei": 2 * 10**13,
                "claim_gas_wei": 10**13,
                "inclusion_latency_seconds": 8,
                "min_expected_value_wei": 10**12,
            },
        ]
    }


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


def test_parse_sensitivity_scenarios_requires_explicit_costs() -> None:
    scenarios = parse_sensitivity_scenarios(
        _scenario_payload(),
        base_config=_config(),
    )
    by_name = {item.name: item for item in scenarios}

    assert by_name["base"].config.bet_gas_wei == 10**13
    assert by_name["base"].config.claim_gas_wei == 5 * 10**12
    assert by_name["base"].config.inclusion_latency_seconds == 3
    assert by_name["stress"].config.min_expected_value_wei == 10**12


def test_parse_sensitivity_scenarios_rejects_missing_unknown_and_bool_costs() -> None:
    missing = _scenario_payload()
    assert isinstance(missing["scenarios"], list)
    first = missing["scenarios"][0]
    assert isinstance(first, dict)
    del first["bet_gas_wei"]
    with pytest.raises(ValueError, match="missing fields: bet_gas_wei"):
        parse_sensitivity_scenarios(missing, base_config=_config())

    unknown = _scenario_payload()
    assert isinstance(unknown["scenarios"], list)
    first_unknown = unknown["scenarios"][0]
    assert isinstance(first_unknown, dict)
    first_unknown["future_pool_wei"] = 123
    with pytest.raises(ValueError, match="unknown fields: future_pool_wei"):
        parse_sensitivity_scenarios(unknown, base_config=_config())

    boolean_cost = _scenario_payload()
    assert isinstance(boolean_cost["scenarios"], list)
    first_bool = boolean_cost["scenarios"][0]
    assert isinstance(first_bool, dict)
    first_bool["bet_gas_wei"] = True
    with pytest.raises(ValueError, match="bet_gas_wei must be an integer"):
        parse_sensitivity_scenarios(boolean_cost, base_config=_config())


def test_validate_scenarios_rejects_untyped_boundary_input() -> None:
    with pytest.raises(TypeError, match="invalid type"):
        _validate_scenarios((object(), object()))


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
