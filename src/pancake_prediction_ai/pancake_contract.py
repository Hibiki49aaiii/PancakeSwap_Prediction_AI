from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .economics import PoolState


BNB_CHAIN_ID = 56
BNB_PREDICTION_CONTRACT = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
ONCHAIN_TREASURY_FEE_DENOMINATOR = 10_000


@dataclass(frozen=True, slots=True)
class PredictionRoundState:
    epoch: int
    start_timestamp: int
    lock_timestamp: int
    close_timestamp: int
    lock_price: int
    close_price: int
    lock_oracle_id: int
    close_oracle_id: int
    total_amount_wei: int
    bull_amount_wei: int
    bear_amount_wei: int
    reward_base_cal_amount_wei: int
    reward_amount_wei: int
    oracle_called: bool

    def validate(self) -> None:
        integer_fields = (
            self.epoch,
            self.start_timestamp,
            self.lock_timestamp,
            self.close_timestamp,
            self.lock_oracle_id,
            self.close_oracle_id,
            self.total_amount_wei,
            self.bull_amount_wei,
            self.bear_amount_wei,
            self.reward_base_cal_amount_wei,
            self.reward_amount_wei,
        )
        if any(value < 0 for value in integer_fields):
            raise ValueError("round unsigned fields must be non-negative")
        if self.total_amount_wei != self.bull_amount_wei + self.bear_amount_wei:
            raise ValueError("round total must equal bull + bear")


def onchain_treasury_fee_to_ppm(value: int) -> int:
    """Convert Pancake Prediction's 1/10,000 treasury units to ppm.

    The upstream contract documents 200 = 2% and 1000 = 10%. Internal economic
    calculations use ppm, so the unit boundary is explicit rather than implicit.
    """

    if not 0 <= value <= 1_000:
        raise ValueError("Prediction treasury fee must be in [0, 1000]")
    return value * 100


def parse_round_result(values: Sequence[object]) -> PredictionRoundState:
    """Normalize the public `rounds(epoch)` tuple from PancakePredictionV2/V3."""

    if len(values) != 14:
        raise ValueError(f"expected 14 round fields, got {len(values)}")
    state = PredictionRoundState(
        epoch=int(values[0]),
        start_timestamp=int(values[1]),
        lock_timestamp=int(values[2]),
        close_timestamp=int(values[3]),
        lock_price=int(values[4]),
        close_price=int(values[5]),
        lock_oracle_id=int(values[6]),
        close_oracle_id=int(values[7]),
        total_amount_wei=int(values[8]),
        bull_amount_wei=int(values[9]),
        bear_amount_wei=int(values[10]),
        reward_base_cal_amount_wei=int(values[11]),
        reward_amount_wei=int(values[12]),
        oracle_called=bool(values[13]),
    )
    state.validate()
    return state


def pool_state_from_round(round_state: PredictionRoundState, *, treasury_fee_units: int) -> PoolState:
    round_state.validate()
    return PoolState(
        bull_wei=round_state.bull_amount_wei,
        bear_wei=round_state.bear_amount_wei,
        treasury_fee_ppm=onchain_treasury_fee_to_ppm(treasury_fee_units),
    )
