from pancake_prediction.economics import (
    ParimutuelQuote,
    breakeven_probability_ppm,
    edge_over_breakeven_ppm,
    expected_value_wei,
    gross_payout_if_win_wei,
)


def test_balanced_pool_requires_more_than_fifty_percent_after_fee() -> None:
    quote = ParimutuelQuote(
        side="bull",
        side_pool_wei=10**18,
        opposing_pool_wei=10**18,
        stake_wei=10**15,
        fee_bps=300,
    )
    threshold = breakeven_probability_ppm(quote)
    assert threshold is not None
    assert threshold > 500_000
    assert expected_value_wei(quote, win_probability_ppm=threshold) >= 0
    assert expected_value_wei(quote, win_probability_ppm=threshold - 1) < 0


def test_large_own_stake_increases_dilution_threshold() -> None:
    small = ParimutuelQuote("bull", 10**18, 10**18, 10**15, 300)
    large = ParimutuelQuote("bull", 10**18, 10**18, 5 * 10**17, 300)
    small_threshold = breakeven_probability_ppm(small)
    large_threshold = breakeven_probability_ppm(large)
    assert small_threshold is not None
    assert large_threshold is not None
    assert large_threshold > small_threshold


def test_gas_costs_raise_required_probability() -> None:
    no_gas = ParimutuelQuote("bear", 10**18, 2 * 10**18, 10**16, 300)
    with_gas = ParimutuelQuote(
        "bear",
        10**18,
        2 * 10**18,
        10**16,
        300,
        bet_gas_wei=10**14,
        claim_gas_wei=10**14,
    )
    no_gas_threshold = breakeven_probability_ppm(no_gas)
    with_gas_threshold = breakeven_probability_ppm(with_gas)
    assert no_gas_threshold is not None
    assert with_gas_threshold is not None
    assert with_gas_threshold > no_gas_threshold


def test_edge_is_model_probability_minus_stake_specific_breakeven() -> None:
    quote = ParimutuelQuote("bull", 4 * 10**18, 6 * 10**18, 10**16, 300)
    threshold = breakeven_probability_ppm(quote)
    assert threshold is not None
    assert edge_over_breakeven_ppm(quote, win_probability_ppm=threshold + 12_345) == 12_345
    assert gross_payout_if_win_wei(quote) > quote.stake_wei
