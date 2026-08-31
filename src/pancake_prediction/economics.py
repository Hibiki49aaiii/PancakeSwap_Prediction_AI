from __future__ import annotations

from dataclasses import dataclass

PPM = 1_000_000
BPS = 10_000


@dataclass(frozen=True, slots=True)
class ParimutuelQuote:
    side: str
    side_pool_wei: int
    opposing_pool_wei: int
    stake_wei: int
    fee_bps: int
    bet_gas_wei: int = 0
    claim_gas_wei: int = 0

    def validate(self) -> None:
        if self.side not in ("bull", "bear"):
            raise ValueError("side must be bull or bear")
        if self.side_pool_wei < 0 or self.opposing_pool_wei < 0:
            raise ValueError("pool values must be non-negative")
        if self.stake_wei <= 0:
            raise ValueError("stake_wei must be positive")
        if not 0 <= self.fee_bps < BPS:
            raise ValueError("fee_bps must be in [0, 10000)")
        if self.bet_gas_wei < 0 or self.claim_gas_wei < 0:
            raise ValueError("gas costs must be non-negative")

    @property
    def total_pool_wei(self) -> int:
        return self.side_pool_wei + self.opposing_pool_wei


def gross_payout_if_win_wei(quote: ParimutuelQuote) -> int:
    """Return total amount received on a win, including returned stake."""
    quote.validate()
    total_with_stake = quote.total_pool_wei + quote.stake_wei
    side_with_stake = quote.side_pool_wei + quote.stake_wei
    distributable = total_with_stake * (BPS - quote.fee_bps) // BPS
    return distributable * quote.stake_wei // side_with_stake


def breakeven_probability_ppm(quote: ParimutuelQuote) -> int | None:
    """Minimum win probability for non-negative EV under the observed pool.

    Claim gas is paid only on a win. Bet gas is paid regardless of outcome. The result is rounded
    upward so the threshold is conservative rather than optimistic.
    """
    gross = gross_payout_if_win_wei(quote)
    win_cash_after_claim_gas = gross - quote.claim_gas_wei
    if win_cash_after_claim_gas <= 0:
        return None
    required = quote.stake_wei + quote.bet_gas_wei
    threshold = (required * PPM + win_cash_after_claim_gas - 1) // win_cash_after_claim_gas
    if threshold > PPM:
        return None
    return threshold


def expected_value_wei(quote: ParimutuelQuote, *, win_probability_ppm: int) -> int:
    quote.validate()
    if not 0 <= win_probability_ppm <= PPM:
        raise ValueError("win_probability_ppm must be in [0, 1_000_000]")
    gross = gross_payout_if_win_wei(quote)
    expected_cash = win_probability_ppm * (gross - quote.claim_gas_wei) // PPM
    return expected_cash - quote.stake_wei - quote.bet_gas_wei


def edge_over_breakeven_ppm(
    quote: ParimutuelQuote, *, win_probability_ppm: int
) -> int | None:
    if not 0 <= win_probability_ppm <= PPM:
        raise ValueError("win_probability_ppm must be in [0, 1_000_000]")
    threshold = breakeven_probability_ppm(quote)
    if threshold is None:
        return None
    return win_probability_ppm - threshold
