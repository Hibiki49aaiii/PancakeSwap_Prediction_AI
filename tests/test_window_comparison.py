from __future__ import annotations

import copy

import pytest

from pancake_prediction.window_comparison import compare_economic_windows


def _variant(
    feature_set_id: str,
    *,
    removed_family: str | None,
    n_scored: int,
    brier: float,
    skill: float,
    pnl: int,
    roi: int,
) -> dict[str, object]:
    return {
        "feature_set_id": feature_set_id,
        "removed_family": removed_family,
        "n_scored": n_scored,
        "brier_score": brier,
        "brier_skill_score": skill,
        "log_loss": brier + 0.48,
        "ece_10": brier / 2,
        "accuracy": 0.53,
        "trade_count": n_scored - 5,
        "pnl_wei": pnl,
        "roi_ppm": roi,
        "max_drawdown_wei": abs(pnl) // 2,
    }


def _evidence(
    *,
    start: int,
    end: int,
    rows: int,
    variants: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "success": True,
        "profitability_gate_eligible": False,
        "full_historical_gate_satisfied": False,
        "signing_enabled": False,
        "live_broadcast": False,
        "source_window": {
            "market": "BNBUSD",
            "start_timestamp": start,
            "end_timestamp": end,
        },
        "semantic_gate": {
            "ready": True,
            "research_feature_rows": rows,
        },
        "ablation_campaign": {
            "evaluation": {
                "ablation": variants,
            }
        },
    }


def _short() -> dict[str, object]:
    return _evidence(
        start=1_000,
        end=1_000 + 86_400,
        rows=268,
        variants=[
            _variant(
                "full-v1",
                removed_family=None,
                n_scored=159,
                brier=0.28,
                skill=-0.12,
                pnl=100,
                roi=50_000,
            ),
            _variant(
                "without-round_history-v1",
                removed_family="round_history",
                n_scored=159,
                brier=0.27,
                skill=-0.08,
                pnl=130,
                roi=60_000,
            ),
            _variant(
                "without-cex_flow-v1",
                removed_family="cex_flow",
                n_scored=159,
                brier=0.29,
                skill=-0.15,
                pnl=80,
                roi=40_000,
            ),
        ],
    )


def _expanded() -> dict[str, object]:
    return _evidence(
        start=1_000,
        end=1_000 + 259_200,
        rows=790,
        variants=[
            _variant(
                "full-v1",
                removed_family=None,
                n_scored=410,
                brier=0.24,
                skill=0.04,
                pnl=350,
                roi=75_000,
            ),
            _variant(
                "without-round_history-v1",
                removed_family="round_history",
                n_scored=410,
                brier=0.25,
                skill=0.02,
                pnl=390,
                roi=80_000,
            ),
            _variant(
                "without-cex_flow-v1",
                removed_family="cex_flow",
                n_scored=410,
                brier=0.26,
                skill=-0.01,
                pnl=280,
                roi=62_000,
            ),
        ],
    )


def test_compare_economic_windows_reports_metric_and_feature_stability() -> None:
    report = compare_economic_windows(_short(), _expanded())
    payload = report.as_dict()

    delta = payload["full_v1_delta_expanded_minus_short"]
    assert isinstance(delta, dict)
    assert delta["n_scored"] == 251
    assert delta["research_feature_rows"] == 522
    assert delta["brier_score"] == pytest.approx(-0.04)
    assert delta["brier_skill_score"] == pytest.approx(0.16)
    assert delta["pnl_wei"] == 250
    assert delta["roi_ppm"] == 25_000

    flags = payload["interpretation_flags"]
    assert isinstance(flags, dict)
    assert flags["expanded_brier_score_improved"] is True
    assert flags["expanded_brier_skill_positive"] is True
    assert flags["expanded_positive_pnl"] is True
    assert flags["best_brier_feature_set_stable"] is False
    assert flags["best_pnl_feature_set_stable"] is True

    stability = payload["feature_stability"]
    assert isinstance(stability, dict)
    assert stability["common_feature_set_count"] == 3
    assert stability["short_best_brier_feature_set"] == "without-round_history-v1"
    assert stability["expanded_best_brier_feature_set"] == "full-v1"
    assert stability["short_best_pnl_feature_set"] == "without-round_history-v1"
    assert stability["expanded_best_pnl_feature_set"] == "without-round_history-v1"

    digest = payload["comparison_digest"]
    assert isinstance(digest, str)
    assert len(digest) == 64
    assert digest == compare_economic_windows(_short(), _expanded()).comparison_digest
    assert payload["profitability_gate_eligible"] is False
    assert payload["signing_enabled"] is False
    assert payload["live_broadcast"] is False


def test_compare_economic_windows_rejects_weakened_evidence_boundary() -> None:
    bad = _expanded()
    bad["profitability_gate_eligible"] = True
    with pytest.raises(ValueError, match="profitability gate boundary"):
        compare_economic_windows(_short(), bad)

    bad = _expanded()
    bad["signing_enabled"] = True
    with pytest.raises(ValueError, match="signing must remain disabled"):
        compare_economic_windows(_short(), bad)

    bad = _expanded()
    semantic = bad["semantic_gate"]
    assert isinstance(semantic, dict)
    semantic["ready"] = False
    with pytest.raises(ValueError, match="semantic gate is not ready"):
        compare_economic_windows(_short(), bad)


def test_compare_economic_windows_rejects_non_expanded_or_duplicate_variants() -> None:
    same_duration = _expanded()
    window = same_duration["source_window"]
    assert isinstance(window, dict)
    window["end_timestamp"] = 1_000 + 86_400
    with pytest.raises(ValueError, match="must be longer"):
        compare_economic_windows(_short(), same_duration)

    duplicate = copy.deepcopy(_expanded())
    campaign = duplicate["ablation_campaign"]
    assert isinstance(campaign, dict)
    evaluation = campaign["evaluation"]
    assert isinstance(evaluation, dict)
    ablation = evaluation["ablation"]
    assert isinstance(ablation, list)
    ablation.append(copy.deepcopy(ablation[0]))
    with pytest.raises(ValueError, match="duplicate feature_set_id"):
        compare_economic_windows(_short(), duplicate)
