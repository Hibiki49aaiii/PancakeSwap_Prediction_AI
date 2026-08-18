from __future__ import annotations

from pancake_prediction.backtest import PoolProjection
from pancake_prediction.pool_projection_dataset import (
    PoolProjectionDataset,
    PoolProjectionDatasetRow,
    PoolProjectionFeatures,
    PoolProjectionLabel,
)
from pancake_prediction.pool_projection_evaluation import evaluate_pool_projection_accuracy


def _row(epoch: int) -> PoolProjectionDatasetRow:
    return PoolProjectionDatasetRow(
        features=PoolProjectionFeatures(
            market="BNBUSD",
            epoch=epoch,
            decision_timestamp=100 * epoch,
            scheduled_lock_timestamp=100 * epoch + 20,
            observed_bull_wei=400,
            observed_bear_wei=600,
            observed_bull_share_ppm=400_000,
            bet_count=10,
            unique_bettors=8,
            bull_flow_5s_wei=40,
            bear_flow_5s_wei=60,
            bull_flow_20s_wei=80,
            bear_flow_20s_wei=120,
            bull_flow_60s_wei=200,
            bear_flow_60s_wei=300,
            flow_imbalance_5s_ppm=-200_000,
            flow_imbalance_20s_ppm=-200_000,
            flow_imbalance_60s_ppm=-200_000,
            prior_bull_rate_20_ppm=500_000,
            prior_abs_return_12_ppm=2_000,
        ),
        label=PoolProjectionLabel(final_bull_wei=800, final_bear_wei=1_200),
    )


def _dataset() -> PoolProjectionDataset:
    return PoolProjectionDataset(
        market="BNBUSD",
        feature_lead_seconds=20,
        replay_digest="a" * 64,
        rows=(_row(1), _row(2)),
        dataset_digest="b" * 64,
    )


def test_pool_projection_accuracy_reports_share_and_total_error() -> None:
    projections = {
        1: PoolProjection(
            epoch=1,
            generated_at=100,
            projected_bull_wei=800,
            projected_bear_wei=1_200,
            model_id="exact",
            train_max_epoch=0,
        ),
        2: PoolProjection(
            epoch=2,
            generated_at=200,
            projected_bull_wei=900,
            projected_bear_wei=1_300,
            model_id="over",
            train_max_epoch=1,
        ),
    }
    report = evaluate_pool_projection_accuracy(_dataset(), projections)

    assert report.scored_rounds == 2
    assert report.missing_projection_rounds == 0
    assert report.mean_abs_total_pool_error_ppm == 50_000
    assert report.median_abs_total_pool_error_ppm == 50_000
    assert report.mean_signed_total_pool_error_ppm == 50_000
    assert report.mean_abs_bull_share_error_ppm == 4_545


def test_pool_projection_accuracy_rejects_post_cutoff_projection() -> None:
    projection = PoolProjection(
        epoch=1,
        generated_at=101,
        projected_bull_wei=800,
        projected_bear_wei=1_200,
        model_id="late",
        train_max_epoch=0,
    )

    try:
        evaluate_pool_projection_accuracy(_dataset(), {1: projection})
    except ValueError as exc:
        assert "after decision cutoff" in str(exc)
    else:
        raise AssertionError("post-cutoff projection must fail closed")
