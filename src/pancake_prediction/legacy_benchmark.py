from __future__ import annotations

from dataclasses import asdict, dataclass

from .backtest import BacktestSignal, PoolProjection
from .economics import PPM, ParimutuelQuote, expected_value_wei, gross_payout_if_win_wei
from .legacy_rounds import LegacyRoundRecord

BPS = 10_000


@dataclass(frozen=True, slots=True)
class LegacyEconomicBenchmarkConfig:
    stake_wei: int
    bet_gas_wei: int
    claim_gas_wei: int
    inclusion_latency_seconds: int
    treasury_fee_bps: int = 300
    decision_lead_seconds: int = 20
    min_expected_value_wei: int = 0
    purge_rounds: int = 2

    def validate(self) -> None:
        if self.stake_wei <= 0:
            raise ValueError("stake_wei must be positive")
        if self.bet_gas_wei < 0 or self.claim_gas_wei < 0:
            raise ValueError("gas costs must be non-negative")
        if self.inclusion_latency_seconds < 0 or self.decision_lead_seconds <= 0:
            raise ValueError("timing values must be non-negative with positive decision lead")
        if not 0 <= self.treasury_fee_bps < BPS:
            raise ValueError("treasury_fee_bps must be in [0, 10000)")
        if self.purge_rounds < 0:
            raise ValueError("purge_rounds must be non-negative")


@dataclass(frozen=True, slots=True)
class LegacyBenchmarkTrade:
    epoch: int
    side: str
    p_bull_ppm: int
    estimated_ev_wei: int
    decision_timestamp: int
    execution_timestamp: int
    lock_timestamp: int
    projected_bull_wei: int
    projected_bear_wei: int
    final_bull_wei: int
    final_bear_wei: int
    outcome: str
    pnl_wei: int
    signal_model_id: str
    projection_model_id: str
    signal_train_max_epoch: int
    projection_train_max_epoch: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LegacyEconomicBenchmarkReport:
    source_class: str
    authoritative: bool
    config: LegacyEconomicBenchmarkConfig
    settled_rounds: int
    trade_count: int
    skipped_refunded: int
    skipped_empty_pool: int
    skipped_missing_signal: int
    skipped_missing_projection: int
    skipped_oos_provenance: int
    skipped_late: int
    skipped_no_positive_ev: int
    pnl_wei: int
    capital_at_risk_wei: int
    roi_ppm: int | None
    max_drawdown_wei: int
    trades: tuple[LegacyBenchmarkTrade, ...]
    limitations: tuple[str, ...]

    def summary(self) -> dict[str, object]:
        return {
            "source_class": self.source_class,
            "authoritative": self.authoritative,
            "config": asdict(self.config),
            "settled_rounds": self.settled_rounds,
            "trade_count": self.trade_count,
            "skipped_refunded": self.skipped_refunded,
            "skipped_empty_pool": self.skipped_empty_pool,
            "skipped_missing_signal": self.skipped_missing_signal,
            "skipped_missing_projection": self.skipped_missing_projection,
            "skipped_oos_provenance": self.skipped_oos_provenance,
            "skipped_late": self.skipped_late,
            "skipped_no_positive_ev": self.skipped_no_positive_ev,
            "pnl_wei": self.pnl_wei,
            "capital_at_risk_wei": self.capital_at_risk_wei,
            "roi_ppm": self.roi_ppm,
            "max_drawdown_wei": self.max_drawdown_wei,
            "limitations": self.limitations,
        }


def _quote(
    side: str,
    bull_pool_wei: int,
    bear_pool_wei: int,
    config: LegacyEconomicBenchmarkConfig,
) -> ParimutuelQuote:
    return ParimutuelQuote(
        side=side,
        side_pool_wei=bull_pool_wei if side == "bull" else bear_pool_wei,
        opposing_pool_wei=bear_pool_wei if side == "bull" else bull_pool_wei,
        stake_wei=config.stake_wei,
        fee_bps=config.treasury_fee_bps,
        bet_gas_wei=config.bet_gas_wei,
        claim_gas_wei=config.claim_gas_wei,
    )


def _realized_pnl(
    record: LegacyRoundRecord,
    side: str,
    config: LegacyEconomicBenchmarkConfig,
) -> int:
    if record.label != side:
        return -config.stake_wei - config.bet_gas_wei
    quote = _quote(
        side,
        record.bull_amount_wei,
        record.bear_amount_wei,
        config,
    )
    gross = gross_payout_if_win_wei(quote)
    return gross - config.stake_wei - config.bet_gas_wei - config.claim_gas_wei


def _purged_train_max_ok(
    epoch: int,
    train_max_epoch: int | None,
    purge_rounds: int,
) -> bool:
    if train_max_epoch is None:
        return False
    return train_max_epoch <= epoch - purge_rounds - 1


def _max_drawdown(trades: tuple[LegacyBenchmarkTrade, ...]) -> int:
    equity = 0
    peak = 0
    drawdown = 0
    for trade in trades:
        equity += trade.pnl_wei
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def run_legacy_economic_benchmark(
    rounds: tuple[LegacyRoundRecord, ...],
    signals: dict[int, BacktestSignal],
    projections: dict[int, PoolProjection],
    config: LegacyEconomicBenchmarkConfig,
) -> LegacyEconomicBenchmarkReport:
    config.validate()
    trades: list[LegacyBenchmarkTrade] = []
    skipped_refunded = 0
    skipped_empty_pool = 0
    skipped_missing_signal = 0
    skipped_missing_projection = 0
    skipped_oos_provenance = 0
    skipped_late = 0
    skipped_no_positive_ev = 0
    settled_rounds = 0

    for record in rounds:
        if not record.oracle_called:
            skipped_refunded += 1
            continue
        settled_rounds += 1
        if record.bull_amount_wei <= 0 or record.bear_amount_wei <= 0:
            skipped_empty_pool += 1
            continue
        decision_timestamp = record.lock_timestamp - config.decision_lead_seconds
        if decision_timestamp <= record.start_timestamp:
            skipped_late += 1
            continue

        signal = signals.get(record.epoch)
        if signal is None:
            skipped_missing_signal += 1
            continue
        projection = projections.get(record.epoch)
        if projection is None:
            skipped_missing_projection += 1
            continue
        signal.validate()
        projection.validate()
        if signal.epoch != record.epoch or projection.epoch != record.epoch:
            raise ValueError("legacy signal/projection epoch mismatch")
        if signal.generated_at > decision_timestamp or projection.generated_at > decision_timestamp:
            skipped_oos_provenance += 1
            continue
        if not _purged_train_max_ok(
            record.epoch,
            signal.train_max_epoch,
            config.purge_rounds,
        ) or not _purged_train_max_ok(
            record.epoch,
            projection.train_max_epoch,
            config.purge_rounds,
        ):
            skipped_oos_provenance += 1
            continue

        execution_timestamp = decision_timestamp + config.inclusion_latency_seconds
        if execution_timestamp >= record.lock_timestamp:
            skipped_late += 1
            continue

        bull_quote = _quote(
            "bull",
            projection.projected_bull_wei,
            projection.projected_bear_wei,
            config,
        )
        bear_quote = _quote(
            "bear",
            projection.projected_bull_wei,
            projection.projected_bear_wei,
            config,
        )
        bull_ev = expected_value_wei(
            bull_quote,
            win_probability_ppm=signal.p_bull_ppm,
        )
        bear_ev = expected_value_wei(
            bear_quote,
            win_probability_ppm=PPM - signal.p_bull_ppm,
        )
        side, estimated_ev = ("bull", bull_ev) if bull_ev >= bear_ev else ("bear", bear_ev)
        if estimated_ev <= config.min_expected_value_wei:
            skipped_no_positive_ev += 1
            continue
        assert signal.train_max_epoch is not None
        assert projection.train_max_epoch is not None
        trades.append(
            LegacyBenchmarkTrade(
                epoch=record.epoch,
                side=side,
                p_bull_ppm=signal.p_bull_ppm,
                estimated_ev_wei=estimated_ev,
                decision_timestamp=decision_timestamp,
                execution_timestamp=execution_timestamp,
                lock_timestamp=record.lock_timestamp,
                projected_bull_wei=projection.projected_bull_wei,
                projected_bear_wei=projection.projected_bear_wei,
                final_bull_wei=record.bull_amount_wei,
                final_bear_wei=record.bear_amount_wei,
                outcome=record.label,
                pnl_wei=_realized_pnl(record, side, config),
                signal_model_id=signal.model_id,
                projection_model_id=projection.model_id,
                signal_train_max_epoch=signal.train_max_epoch,
                projection_train_max_epoch=projection.train_max_epoch,
            )
        )

    trade_tuple = tuple(trades)
    pnl = sum(trade.pnl_wei for trade in trade_tuple)
    capital = len(trade_tuple) * config.stake_wei
    roi_ppm = None if capital <= 0 else pnl * PPM // capital
    return LegacyEconomicBenchmarkReport(
        source_class="third_party_historical_benchmark",
        authoritative=False,
        config=config,
        settled_rounds=settled_rounds,
        trade_count=len(trade_tuple),
        skipped_refunded=skipped_refunded,
        skipped_empty_pool=skipped_empty_pool,
        skipped_missing_signal=skipped_missing_signal,
        skipped_missing_projection=skipped_missing_projection,
        skipped_oos_provenance=skipped_oos_provenance,
        skipped_late=skipped_late,
        skipped_no_positive_ev=skipped_no_positive_ev,
        pnl_wei=pnl,
        capital_at_risk_wei=capital,
        roi_ppm=roi_ppm,
        max_drawdown_wei=_max_drawdown(trade_tuple),
        trades=trade_tuple,
        limitations=(
            "third-party dataset; not authoritative on-chain event evidence",
            "pool amounts are rounded from approximately six significant digits",
            "target-round pre-decision bet flow is unavailable and must not be fabricated",
            "result is supporting evidence only and cannot pass the profitability gate",
        ),
    )
