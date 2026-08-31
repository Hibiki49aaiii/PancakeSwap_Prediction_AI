from pancake_prediction.backtest import BacktestConfig, PoolProjection
from pancake_prediction.baseline import ALL_FEATURE_NAMES, ResearchFeatureRow
from pancake_prediction.economic_ablation import run_economic_feature_ablation
from pancake_prediction.replay import ReplaySnapshot, RoundRecord


def _round(epoch: int, label: str) -> RoundRecord:
    start = epoch * 1_000
    return RoundRecord(
        epoch=epoch,
        start_block=epoch,
        start_timestamp=start,
        lock_block=epoch,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=100,
        end_block=epoch,
        end_timestamp=start + 600,
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
    values = {name: 0.0 for name in ALL_FEATURE_NAMES}
    values["spot_oracle_gap_ppm"] = signal
    values["spot_flow_imbalance_ppm"] = signal * 0.8
    values["pool_bull_share_ppm"] = 500_000.0 + signal
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=epoch,
        decision_timestamp_ms=(epoch * 1_000 + 280) * 1_000,
        values=values,
    )


def test_economic_ablation_uses_same_common_epochs_for_every_variant() -> None:
    rounds = tuple(
        _round(epoch, "bull" if epoch % 4 >= 2 else "bear")
        for epoch in range(1, 81)
    )
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, rounds)
    rows = tuple(
        _row(epoch, 150_000.0 if epoch % 4 >= 2 else -150_000.0)
        for epoch in range(1, 81)
    )
    projections = {
        epoch: PoolProjection(
            epoch=epoch,
            generated_at=epoch * 1_000 + 280,
            projected_bull_wei=100,
            projected_bear_wei=100,
            model_id="pool-oos",
            train_max_epoch=max(0, epoch - 3),
        )
        for epoch in range(3, 81)
    }
    results = run_economic_feature_ablation(
        replay,
        (),
        rows,
        projections,
        BacktestConfig(stake_wei=10, decision_lead_seconds=20),
        min_train_rounds=30,
        test_rounds=10,
        purge_rounds=2,
        embargo_rounds=1,
        calibration_rounds=10,
    )
    assert len(results) == 5
    common_counts = {result.common_epoch_count for result in results}
    assert len(common_counts) == 1
    assert next(iter(common_counts)) > 0
    assert results[0].removed_family is None
    assert results[0].n_features == len(ALL_FEATURE_NAMES)
    assert all(result.n_scored == result.common_epoch_count for result in results)
