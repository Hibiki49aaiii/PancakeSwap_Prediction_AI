from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Side(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"


@dataclass(frozen=True, slots=True)
class PoolState:
    bull_wei: int
    bear_wei: int
    treasury_fee_ppm: int

    def validate(self) -> None:
        if self.bull_wei < 0 or self.bear_wei < 0:
            raise ValueError("pool amounts must be non-negative")
        if not 0 <= self.treasury_fee_ppm < 1_000_000:
            raise ValueError("treasury_fee_ppm must be in [0, 1_000_000)")


@dataclass(frozen=True, slots=True)
class ExecutionCost:
    gas_cost_wei: int = 0
    same_side_inflow_wei: int = 0
    opposite_side_inflow_wei: int = 0
    execution_success_probability: float = 1.0

    def validate(self) -> None:
        if self.gas_cost_wei < 0:
            raise ValueError("gas_cost_wei must be non-negative")
        if self.same_side_inflow_wei < 0 or self.opposite_side_inflow_wei < 0:
            raise ValueError("post-decision inflows must be non-negative")
        if not 0.0 <= self.execution_success_probability <= 1.0:
            raise ValueError("execution_success_probability must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class EVResult:
    side: Side
    probability_win: float
    gross_payout_if_win_wei: float
    pnl_if_win_wei: float
    pnl_if_lose_wei: float
    expected_pnl_if_executed_wei: float
    expected_pnl_wei: float
    expected_return_on_stake: float
    break_even_probability: float

    @property
    def positive(self) -> bool:
        return self.expected_pnl_wei > 0


def evaluate_bet_ev(
    *,
    probability_bull: float,
    side: Side,
    stake_wei: int,
    pool: PoolState,
    cost: ExecutionCost = ExecutionCost(),
) -> EVResult:
    """Evaluate one binary Prediction bet using diluted pool economics.

    The user's own stake is included in the winning-side denominator. Optional
    same/opposite-side inflows represent conservative expected pool movement
    after the decision snapshot but before lock. Gas is charged whenever an
    execution attempt succeeds. If execution itself fails, expected PnL is
    treated as zero here; separate reconciliation costs belong in execution
    accounting rather than being hidden in model accuracy.
    """

    pool.validate()
    cost.validate()
    if not 0.0 <= probability_bull <= 1.0:
        raise ValueError("probability_bull must be in [0, 1]")
    if stake_wei <= 0:
        raise ValueError("stake_wei must be positive")

    p_win = probability_bull if side is Side.BULL else 1.0 - probability_bull
    own_side = pool.bull_wei if side is Side.BULL else pool.bear_wei
    other_side = pool.bear_wei if side is Side.BULL else pool.bull_wei

    winning_side_pool = own_side + stake_wei + cost.same_side_inflow_wei
    losing_side_pool = other_side + cost.opposite_side_inflow_wei
    total_pool = winning_side_pool + losing_side_pool
    distributable = total_pool * (1_000_000 - pool.treasury_fee_ppm) / 1_000_000

    if winning_side_pool <= 0:
        raise ValueError("winning-side pool must be positive after stake")

    gross_payout = stake_wei / winning_side_pool * distributable
    pnl_win = gross_payout - stake_wei - cost.gas_cost_wei
    pnl_lose = -stake_wei - cost.gas_cost_wei
    expected_if_executed = p_win * pnl_win + (1.0 - p_win) * pnl_lose
    expected = cost.execution_success_probability * expected_if_executed
    expected_return = expected / stake_wei

    denominator = gross_payout
    if denominator <= 0:
        break_even = 1.0
    else:
        # p * gross_payout - stake - gas = 0
        break_even = (stake_wei + cost.gas_cost_wei) / denominator

    return EVResult(
        side=side,
        probability_win=p_win,
        gross_payout_if_win_wei=gross_payout,
        pnl_if_win_wei=pnl_win,
        pnl_if_lose_wei=pnl_lose,
        expected_pnl_if_executed_wei=expected_if_executed,
        expected_pnl_wei=expected,
        expected_return_on_stake=expected_return,
        break_even_probability=break_even,
    )
