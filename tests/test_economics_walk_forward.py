from __future__ import annotations

import math

from pancake_prediction_ai.economics import ExecutionCost, PoolState, Side, evaluate_bet_ev
from pancake_prediction_ai.walk_forward import purged_walk_forward_splits


def test_ev_includes_own_bet_dilution() -> None:
    result = evaluate_bet_ev(
        probability_bull=0.60,
        side=Side.BULL,
        stake_wei=10,
        pool=PoolState(bull_wei=100, bear_wei=100, treasury_fee_ppm=0),
    )
    assert math.isclose(result.gross_payout_if_win_wei, 10 / 110 * 210)
    assert result.break_even_probability > 0.52
    assert result.positive


def test_same_side_post_decision_inflow_reduces_ev() -> None:
    base = evaluate_bet_ev(
        probability_bull=0.60,
        side=Side.BULL,
        stake_wei=10,
        pool=PoolState(100, 100, 30_000),
        cost=ExecutionCost(gas_cost_wei=1),
    )
    crowded = evaluate_bet_ev(
        probability_bull=0.60,
        side=Side.BULL,
        stake_wei=10,
        pool=PoolState(100, 100, 30_000),
        cost=ExecutionCost(gas_cost_wei=1, same_side_inflow_wei=100),
    )
    assert crowded.gross_payout_if_win_wei < base.gross_payout_if_win_wei
    assert crowded.expected_pnl_wei < base.expected_pnl_wei


def test_execution_probability_scales_ev_not_model_probability() -> None:
    full = evaluate_bet_ev(
        probability_bull=0.7,
        side=Side.BULL,
        stake_wei=10,
        pool=PoolState(100, 100, 0),
    )
    half = evaluate_bet_ev(
        probability_bull=0.7,
        side=Side.BULL,
        stake_wei=10,
        pool=PoolState(100, 100, 0),
        cost=ExecutionCost(execution_success_probability=0.5),
    )
    assert half.probability_win == full.probability_win
    assert math.isclose(half.expected_pnl_wei, full.expected_pnl_wei * 0.5)


def test_purged_walk_forward_has_no_train_test_boundary_overlap() -> None:
    splits = purged_walk_forward_splits(
        100,
        min_train_size=40,
        test_size=10,
        purge_size=5,
    )
    assert splits
    for split in splits:
        assert split.train_stop + 5 == split.test_start
        assert set(split.train_indices).isdisjoint(split.test_indices)
        assert len(split.test_indices) == 10


def test_rolling_walk_forward_respects_max_train_size() -> None:
    splits = purged_walk_forward_splits(
        120,
        min_train_size=30,
        test_size=10,
        purge_size=3,
        max_train_size=40,
    )
    assert splits
    assert all(len(split.train_indices) <= 40 for split in splits)
    assert len(splits[-1].train_indices) == 40
