from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .backtest import BacktestSignal, PoolProjection
from .economics import PPM, ParimutuelQuote, expected_value_wei
from .legacy_benchmark import LegacyEconomicBenchmarkConfig
from .legacy_rounds import LegacyRoundRecord


@dataclass(frozen=True, slots=True)
class LegacyPoolProjectionDiagnostics:
    selected_trades: int
    projected_positive_final_negative: int
    projected_side_differs_from_final_pool_side: int
    projected_ev_sum_wei: int
    final_pool_ev_sum_wei: int
    projected_ev_optimism_wei: int
    mean_abs_bull_share_error_ppm: int | None
    mean_abs_total_pool_error_ppm: int | None
    diagnostic_uses_final_pool: bool = True
    tradeable_feature: bool = False
    profitability_gate_eligible: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _quote(
    side: str,
    bull_wei: int,
    bear_wei: int,
    config: LegacyEconomicBenchmarkConfig,
) -> ParimutuelQuote:
    return ParimutuelQuote(
        side=side,
        side_pool_wei=bull_wei if side == "bull" else bear_wei,
        opposing_pool_wei=bear_wei if side == "bull" else bull_wei,
        stake_wei=config.stake_wei,
        fee_bps=config.treasury_fee_bps,
        bet_gas_wei=config.bet_gas_wei,
        claim_gas_wei=config.claim_gas_wei,
    )


def _bull_share_ppm(bull_wei: int, bear_wei: int) -> int | None:
    total = bull_wei + bear_wei
    if total <= 0:
        return None
    return bull_wei * PPM // total


def _relative_abs_error_ppm(projected: int, actual: int) -> int | None:
    if actual <= 0:
        return None
    return abs(projected - actual) * PPM // actual


def diagnose_legacy_pool_projection(
    rounds: tuple[LegacyRoundRecord, ...],
    signals: Mapping[int, BacktestSignal],
    projections: Mapping[int, PoolProjection],
    config: LegacyEconomicBenchmarkConfig,
) -> LegacyPoolProjectionDiagnostics:
    config.validate()
    selected = 0
    projected_positive_final_negative = 0
    side_changes = 0
    projected_ev_sum = 0
    final_ev_sum = 0
    bull_share_errors: list[int] = []
    total_pool_errors: list[int] = []

    for record in sorted(rounds, key=lambda item: item.epoch):
        if not record.oracle_called or record.label not in {"bull", "bear"}:
            continue
        signal = signals.get(record.epoch)
        projection = projections.get(record.epoch)
        if signal is None or projection is None:
            continue
        signal.validate()
        projection.validate()
        if signal.epoch != record.epoch or projection.epoch != record.epoch:
            raise ValueError("diagnostic signal/projection epoch mismatch")

        p_bull = signal.p_bull_ppm
        projected_bull_ev = expected_value_wei(
            _quote(
                "bull",
                projection.projected_bull_wei,
                projection.projected_bear_wei,
                config,
            ),
            win_probability_ppm=p_bull,
        )
        projected_bear_ev = expected_value_wei(
            _quote(
                "bear",
                projection.projected_bull_wei,
                projection.projected_bear_wei,
                config,
            ),
            win_probability_ppm=PPM - p_bull,
        )
        projected_side, projected_ev = (
            ("bull", projected_bull_ev)
            if projected_bull_ev >= projected_bear_ev
            else ("bear", projected_bear_ev)
        )
        if projected_ev <= config.min_expected_value_wei:
            continue

        final_bull_ev = expected_value_wei(
            _quote("bull", record.bull_amount_wei, record.bear_amount_wei, config),
            win_probability_ppm=p_bull,
        )
        final_bear_ev = expected_value_wei(
            _quote("bear", record.bull_amount_wei, record.bear_amount_wei, config),
            win_probability_ppm=PPM - p_bull,
        )
        final_side, final_best_ev = (
            ("bull", final_bull_ev)
            if final_bull_ev >= final_bear_ev
            else ("bear", final_bear_ev)
        )
        final_selected_ev = final_bull_ev if projected_side == "bull" else final_bear_ev

        selected += 1
        projected_ev_sum += projected_ev
        final_ev_sum += final_selected_ev
        if final_selected_ev <= config.min_expected_value_wei:
            projected_positive_final_negative += 1
        if final_side != projected_side and final_best_ev > config.min_expected_value_wei:
            side_changes += 1

        projected_share = _bull_share_ppm(
            projection.projected_bull_wei,
            projection.projected_bear_wei,
        )
        final_share = _bull_share_ppm(record.bull_amount_wei, record.bear_amount_wei)
        if projected_share is not None and final_share is not None:
            bull_share_errors.append(abs(projected_share - final_share))
        total_error = _relative_abs_error_ppm(
            projection.projected_bull_wei + projection.projected_bear_wei,
            record.bull_amount_wei + record.bear_amount_wei,
        )
        if total_error is not None:
            total_pool_errors.append(total_error)

    mean_share_error = (
        None if not bull_share_errors else sum(bull_share_errors) // len(bull_share_errors)
    )
    mean_total_error = (
        None if not total_pool_errors else sum(total_pool_errors) // len(total_pool_errors)
    )
    return LegacyPoolProjectionDiagnostics(
        selected_trades=selected,
        projected_positive_final_negative=projected_positive_final_negative,
        projected_side_differs_from_final_pool_side=side_changes,
        projected_ev_sum_wei=projected_ev_sum,
        final_pool_ev_sum_wei=final_ev_sum,
        projected_ev_optimism_wei=projected_ev_sum - final_ev_sum,
        mean_abs_bull_share_error_ppm=mean_share_error,
        mean_abs_total_pool_error_ppm=mean_total_error,
    )
