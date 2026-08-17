import pytest

from pancake_prediction.replay import ReplaySnapshot, RoundRecord
from pancake_prediction.walkforward import (
    OosSignal,
    evaluate_oos,
    generate_expanding_folds,
    validate_oos_provenance,
)


def _round(epoch: int, label: str) -> RoundRecord:
    return RoundRecord(
        epoch=epoch,
        start_block=epoch,
        start_timestamp=epoch * 100,
        lock_block=epoch,
        lock_timestamp=epoch * 100 + 50,
        lock_round_id=epoch,
        lock_price=100,
        end_block=epoch,
        end_timestamp=epoch * 100 + 80,
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


def test_expanding_folds_apply_purge_and_embargo() -> None:
    folds = generate_expanding_folds(
        range(1, 21), min_train_rounds=5, test_rounds=4, purge_rounds=2, embargo_rounds=1
    )
    assert folds[0].train_end_epoch == 5
    assert folds[0].test_start_epoch == 8
    assert folds[0].test_end_epoch == 11
    assert folds[1].test_start_epoch == 13


def test_oos_provenance_rejects_training_too_close_to_target() -> None:
    with pytest.raises(ValueError, match="not purged OOS"):
        validate_oos_provenance(
            (OosSignal(epoch=100, p_bull_ppm=600_000, generated_at=10_000, train_max_epoch=99),),
            purge_rounds=2,
        )


def test_oos_metrics_score_only_settled_non_tie_rounds() -> None:
    replay = ReplaySnapshot(
        1,
        "BNBUSD",
        "a" * 64,
        (_round(10, "bull"), _round(11, "bear"), _round(12, "bull")),
    )
    signals = {
        10: OosSignal(10, 800_000, 1000, 7, "wf-1"),
        11: OosSignal(11, 200_000, 1100, 8, "wf-1"),
        12: OosSignal(12, 700_000, 1200, 9, "wf-1"),
    }
    metrics = evaluate_oos(replay, signals, purge_rounds=2)
    assert metrics.n_scored == 3
    assert metrics.accuracy == 1.0
    assert metrics.brier_score is not None and metrics.brier_score < 0.1
    assert metrics.brier_skill_score is not None and metrics.brier_skill_score > 0
    assert metrics.ece_10 is not None
