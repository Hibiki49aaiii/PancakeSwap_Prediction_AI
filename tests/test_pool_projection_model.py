from __future__ import annotations

from pancake_prediction.pool_projection_dataset import (
    PoolProjectionDataset,
    PoolProjectionDatasetRow,
    PoolProjectionFeatures,
    PoolProjectionLabel,
)
from pancake_prediction.pool_projection_model import (
    PoolProjectionKnnConfig,
    build_oos_feature_conditioned_pool_projections,
)


def _row(
    epoch: int,
    *,
    decision: int,
    scheduled_lock: int,
    bull_share: int,
    final_bull: int,
    final_bear: int,
) -> PoolProjectionDatasetRow:
    observed_bull = bull_share
    observed_bear = 1_000 - bull_share
    return PoolProjectionDatasetRow(
        features=PoolProjectionFeatures(
            market="BNBUSD",
            epoch=epoch,
            decision_timestamp=decision,
            scheduled_lock_timestamp=scheduled_lock,
            observed_bull_wei=observed_bull,
            observed_bear_wei=observed_bear,
            observed_bull_share_ppm=bull_share * 1_000,
            bet_count=10 + epoch,
            unique_bettors=5 + epoch,
            bull_flow_5s_wei=bull_share // 10,
            bear_flow_5s_wei=(1_000 - bull_share) // 10,
            bull_flow_20s_wei=bull_share // 5,
            bear_flow_20s_wei=(1_000 - bull_share) // 5,
            bull_flow_60s_wei=bull_share // 2,
            bear_flow_60s_wei=(1_000 - bull_share) // 2,
            flow_imbalance_5s_ppm=(bull_share - (1_000 - bull_share)) * 1_000,
            flow_imbalance_20s_ppm=(bull_share - (1_000 - bull_share)) * 1_000,
            flow_imbalance_60s_ppm=(bull_share - (1_000 - bull_share)) * 1_000,
            prior_bull_rate_20_ppm=500_000,
            prior_abs_return_12_ppm=2_000,
        ),
        label=PoolProjectionLabel(final_bull_wei=final_bull, final_bear_wei=final_bear),
    )


def _dataset(rows: tuple[PoolProjectionDatasetRow, ...]) -> PoolProjectionDataset:
    return PoolProjectionDataset(
        market="BNBUSD",
        feature_lead_seconds=20,
        replay_digest="a" * 64,
        rows=rows,
        dataset_digest="b" * 64,
    )


def test_feature_conditioned_projection_is_purged_and_uses_nearest_prior_state() -> None:
    rows = (
        _row(
            1,
            decision=100,
            scheduled_lock=120,
            bull_share=200,
            final_bull=1_200,
            final_bear=800,
        ),
        _row(
            2,
            decision=200,
            scheduled_lock=220,
            bull_share=800,
            final_bull=800,
            final_bear=1_200,
        ),
        _row(
            3,
            decision=300,
            scheduled_lock=320,
            bull_share=200,
            final_bull=1_200,
            final_bear=800,
        ),
        _row(
            4,
            decision=400,
            scheduled_lock=420,
            bull_share=800,
            final_bull=800,
            final_bear=1_200,
        ),
    )
    report = build_oos_feature_conditioned_pool_projections(
        _dataset(rows),
        config=PoolProjectionKnnConfig(
            min_train_rounds=2,
            window_rounds=3,
            purge_rounds=1,
            neighbors=1,
        ),
    )

    projection = report.projections[4]
    assert projection.train_max_epoch == 2
    assert projection.projected_bull_wei == 800
    assert projection.projected_bear_wei == 1_200
    assert projection.generated_at == 400
    assert projection.model_id.startswith("pool-knn-")


def test_projection_requires_training_label_to_be_known_before_target_decision() -> None:
    rows = (
        _row(
            1,
            decision=100,
            scheduled_lock=120,
            bull_share=200,
            final_bull=1_200,
            final_bear=800,
        ),
        _row(
            2,
            decision=200,
            scheduled_lock=500,
            bull_share=800,
            final_bull=800,
            final_bear=1_200,
        ),
        _row(
            4,
            decision=400,
            scheduled_lock=420,
            bull_share=800,
            final_bull=800,
            final_bear=1_200,
        ),
    )
    report = build_oos_feature_conditioned_pool_projections(
        _dataset(rows),
        config=PoolProjectionKnnConfig(
            min_train_rounds=2,
            window_rounds=3,
            purge_rounds=1,
            neighbors=1,
        ),
    )

    assert 4 not in report.projections
    assert report.skipped_insufficient_history >= 1


def test_projection_config_rejects_invalid_neighbor_count() -> None:
    try:
        PoolProjectionKnnConfig(
            min_train_rounds=2,
            window_rounds=3,
            purge_rounds=1,
            neighbors=4,
        ).validate()
    except ValueError as exc:
        assert "neighbors" in str(exc)
    else:
        raise AssertionError("neighbors above window size must fail closed")
