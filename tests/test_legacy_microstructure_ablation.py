from __future__ import annotations

from pancake_prediction.legacy_microstructure_ablation import ABLATION_FEATURE_SETS
from pancake_prediction.legacy_microstructure_model import (
    LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES,
)
from pancake_prediction.legacy_model import LEGACY_FEATURE_NAMES


def test_ablation_feature_sets_are_unique_subsets_of_frozen_v2() -> None:
    frozen = set(LEGACY_MICROSTRUCTURE_V2_FEATURE_NAMES)
    assert tuple(ABLATION_FEATURE_SETS) == (
        "v2_all",
        "spot_micro",
        "perp_micro",
        "returns",
        "flow",
        "intensity",
        "horizon_5s",
        "horizon_20s",
        "horizon_60s",
    )
    for feature_names in ABLATION_FEATURE_SETS.values():
        assert len(feature_names) == len(set(feature_names))
        assert set(feature_names) <= frozen
        assert feature_names[: len(LEGACY_FEATURE_NAMES)] == LEGACY_FEATURE_NAMES


def test_ablation_feature_set_sizes_match_design() -> None:
    assert len(ABLATION_FEATURE_SETS["v2_all"]) == 23
    assert len(ABLATION_FEATURE_SETS["spot_micro"]) == 14
    assert len(ABLATION_FEATURE_SETS["perp_micro"]) == 14
    assert len(ABLATION_FEATURE_SETS["returns"]) == 11
    assert len(ABLATION_FEATURE_SETS["flow"]) == 11
    assert len(ABLATION_FEATURE_SETS["intensity"]) == 11
    assert len(ABLATION_FEATURE_SETS["horizon_5s"]) == 11
    assert len(ABLATION_FEATURE_SETS["horizon_20s"]) == 11
    assert len(ABLATION_FEATURE_SETS["horizon_60s"]) == 11


def test_horizon_ablation_contains_both_venues_and_all_microstructure_types() -> None:
    five = set(ABLATION_FEATURE_SETS["horizon_5s"])
    expected = {
        "spot_return_5s_ppm",
        "spot_flow_imbalance_5s_ppm",
        "spot_trade_count_5s",
        "perp_return_5s_ppm",
        "perp_flow_imbalance_5s_ppm",
        "perp_trade_count_5s",
    }
    assert expected <= five
