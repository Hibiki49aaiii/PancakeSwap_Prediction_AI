import pytest

from pancake_prediction.alpha import (
    TimedPrice,
    build_alpha_feature_row,
    empirical_oracle_update_hazard_ppm,
)


def test_alpha_feature_row_is_decision_time_safe() -> None:
    updates = tuple(range(0, 50_000, 5_000))
    row = build_alpha_feature_row(
        market="BTCUSD",
        epoch=123,
        decision_timestamp_ms=50_000,
        chainlink=TimedPrice("chainlink", 45_000, 6_000_000_000_000),
        spot=TimedPrice("binance_spot", 49_900, 6_006_000_000_000),
        perp=TimedPrice("binance_perp", 49_950, 6_009_000_000_000),
        spot_flow_imbalance_ppm=250_000,
        perp_flow_imbalance_ppm=100_000,
        oracle_update_timestamps_ms=updates,
        oracle_hazard_horizon_ms=5_000,
        oracle_hazard_min_intervals=8,
    )
    assert row.oracle_age_ms == 5_000
    assert row.spot_oracle_gap_ppm == 1_000
    assert row.perp_oracle_gap_ppm == 1_500
    assert row.oracle_update_hazard_ppm == 1_000_000


def test_alpha_rejects_future_observation() -> None:
    with pytest.raises(ValueError, match="after decision cutoff"):
        build_alpha_feature_row(
            market="BTCUSD",
            epoch=123,
            decision_timestamp_ms=50_000,
            chainlink=TimedPrice("chainlink", 45_000, 6_000_000_000_000),
            spot=TimedPrice("binance_spot", 50_001, 6_006_000_000_000),
        )


def test_oracle_hazard_rejects_future_update() -> None:
    with pytest.raises(ValueError, match="future oracle update"):
        empirical_oracle_update_hazard_ppm(
            (1_000, 2_000, 3_000, 11_000),
            decision_timestamp_ms=10_000,
            horizon_ms=1_000,
            min_intervals=1,
        )
