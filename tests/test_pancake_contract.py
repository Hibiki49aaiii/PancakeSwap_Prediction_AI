from __future__ import annotations

import pytest

from pancake_prediction_ai.pancake_contract import (
    BNB_CHAIN_ID,
    BNB_PREDICTION_CONTRACT,
    onchain_treasury_fee_to_ppm,
    parse_round_result,
    pool_state_from_round,
)


def test_current_bnb_prediction_network_config() -> None:
    assert BNB_CHAIN_ID == 56
    assert BNB_PREDICTION_CONTRACT.lower() == "0x18b2a687610328590bc8f2e5fedde3b582a49cda"


def test_onchain_treasury_units_convert_to_ppm_explicitly() -> None:
    assert onchain_treasury_fee_to_ppm(200) == 20_000
    assert onchain_treasury_fee_to_ppm(1000) == 100_000
    with pytest.raises(ValueError):
        onchain_treasury_fee_to_ppm(1001)


def test_round_tuple_normalization_and_pool_conversion() -> None:
    values = (
        123, 1000, 1300, 1600,
        600_00000000, 601_00000000,
        10, 11,
        300, 120, 180,
        180, 291,
        True,
    )
    round_state = parse_round_result(values)
    assert round_state.epoch == 123
    assert round_state.bull_amount_wei == 120
    assert round_state.bear_amount_wei == 180
    pool = pool_state_from_round(round_state, treasury_fee_units=300)
    assert pool.bull_wei == 120
    assert pool.bear_wei == 180
    assert pool.treasury_fee_ppm == 30_000


def test_round_total_invariant_rejects_corrupt_rpc_result() -> None:
    values = (
        123, 1000, 1300, 1600,
        1, 2,
        10, 11,
        999, 120, 180,
        0, 0,
        False,
    )
    with pytest.raises(ValueError, match="total"):
        parse_round_result(values)
