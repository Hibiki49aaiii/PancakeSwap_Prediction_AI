import math

from pancake_prediction.alpha import AlphaFeatureRow
from pancake_prediction.baseline import (
    ALL_FEATURE_NAMES,
    ResearchFeatureRow,
    build_research_feature_row,
    feature_names_without_family,
    fit_logistic_baseline,
    run_feature_family_ablation,
    run_walkforward_baseline,
)
from pancake_prediction.features import PoolFeatureRow
from pancake_prediction.replay import ReplaySnapshot, RoundRecord


def _round(epoch: int, label: str) -> RoundRecord:
    start = epoch * 100
    return RoundRecord(
        epoch=epoch,
        start_block=epoch,
        start_timestamp=start,
        lock_block=epoch,
        lock_timestamp=start + 50,
        lock_round_id=epoch,
        lock_price=100,
        end_block=epoch,
        end_timestamp=start + 80,
        close_round_id=epoch,
        close_price=101 if label == "bull" else 99,
        bull_amount_wei=100,
        bear_amount_wei=100,
        total_amount_wei=200,
        bet_count=2,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label=label,
        issues=(),
    )


def _row(epoch: int, signal: float) -> ResearchFeatureRow:
    start = epoch * 100
    values = {name: 0.0 for name in ALL_FEATURE_NAMES}
    values["spot_oracle_gap_ppm"] = signal
    values["spot_flow_imbalance_ppm"] = signal * 0.7
    values["pool_bull_share_ppm"] = 500_000.0 + signal
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=epoch,
        decision_timestamp_ms=(start + 40) * 1_000,
        values=values,
    )


def test_build_research_feature_row_requires_exact_shared_cutoff() -> None:
    alpha = AlphaFeatureRow(
        market="BNBUSD",
        epoch=7,
        decision_timestamp_ms=12_000,
        chainlink_price_e8=100,
        chainlink_observed_at_ms=10_000,
        oracle_age_ms=2_000,
        spot_price_e8=101,
        spot_observed_at_ms=11_900,
        perp_price_e8=102,
        perp_observed_at_ms=11_950,
        spot_oracle_gap_ppm=10_000,
        perp_oracle_gap_ppm=20_000,
        spot_perp_basis_ppm=9_900,
        spot_flow_imbalance_ppm=100_000,
        perp_flow_imbalance_ppm=50_000,
        oracle_update_hazard_ppm=300_000,
    )
    pool = PoolFeatureRow(
        market="BNBUSD",
        epoch=7,
        feature_timestamp=12,
        scheduled_lock_timestamp=30,
        bull_pool_wei=2 * 10**18,
        bear_pool_wei=10**18,
        bull_share_ppm=666_666,
        bet_count=10,
        unique_bettors=8,
        last_60s_bull_wei=20,
        last_60s_bear_wei=10,
        prior_bull_rate_20_ppm=550_000,
        prior_abs_return_12_ppm=12_000,
    )
    row = build_research_feature_row(alpha=alpha, pool=pool)
    assert row.values["pool_bull_share_ppm"] == 666_666.0
    assert row.values["pool_recent_flow_imbalance_ppm"] is not None
    pool_log_total = row.values["pool_log_total_bnb"]
    assert pool_log_total is not None
    assert math.isclose(pool_log_total, math.log1p(3.0))


def test_logistic_baseline_learns_simple_direction() -> None:
    rows = tuple(_row(epoch, -100_000.0 if epoch <= 20 else 100_000.0) for epoch in range(1, 41))
    outcomes = {epoch: int(epoch > 20) for epoch in range(1, 41)}
    model = fit_logistic_baseline(rows, outcomes, iterations=800)
    assert model.predict_ppm(_row(100, 120_000.0)) > 700_000
    assert model.predict_ppm(_row(101, -120_000.0)) < 300_000


def test_missing_feature_is_imputed_from_training_mean() -> None:
    rows = tuple(_row(epoch, float(epoch * 1000)) for epoch in range(1, 21))
    outcomes = {epoch: epoch % 2 for epoch in range(1, 21)}
    model = fit_logistic_baseline(rows, outcomes)
    values = {name: None for name in ALL_FEATURE_NAMES}
    test_row = ResearchFeatureRow("BNBUSD", 30, 3_040_000, values)
    probability = model.predict_ppm(test_row)
    assert 0 <= probability <= 1_000_000


def test_walkforward_baseline_is_purged_and_calibrated() -> None:
    rounds = tuple(_round(epoch, "bull" if epoch % 4 >= 2 else "bear") for epoch in range(1, 81))
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, rounds)
    rows = tuple(_row(epoch, 150_000.0 if epoch % 4 >= 2 else -150_000.0) for epoch in range(1, 81))
    report = run_walkforward_baseline(
        replay,
        rows,
        min_train_rounds=30,
        test_rounds=10,
        purge_rounds=2,
        embargo_rounds=1,
        calibration_rounds=10,
        calibration_shrinkage=2,
    )
    assert report.metrics.n_scored > 0
    assert report.metrics.brier_score is not None
    assert report.metrics.brier_score < 0.25
    for epoch, signal in report.signals.items():
        assert signal.train_max_epoch <= epoch - 3


def test_feature_family_ablation_returns_full_plus_each_family() -> None:
    rounds = tuple(_round(epoch, "bull" if epoch % 2 else "bear") for epoch in range(1, 61))
    replay = ReplaySnapshot(1, "BNBUSD", "b" * 64, rounds)
    rows = tuple(_row(epoch, 80_000.0 if epoch % 2 else -80_000.0) for epoch in range(1, 61))
    results = run_feature_family_ablation(
        replay,
        rows,
        min_train_rounds=25,
        test_rounds=10,
        purge_rounds=2,
        embargo_rounds=1,
        calibration_rounds=8,
    )
    assert len(results) == 1 + 4
    assert results[0].removed_family is None
    assert feature_names_without_family("cex_flow") != ALL_FEATURE_NAMES
