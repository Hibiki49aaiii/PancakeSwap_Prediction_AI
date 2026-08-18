from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from .backtest import BacktestConfig, PoolProjection, build_decision_snapshot, build_event_index
from .economics import PPM, ParimutuelQuote, expected_value_wei, gross_payout_if_win_wei
from .replay import ChainEvent, ReplaySnapshot, RoundRecord
from .walkforward import OosSignal, validate_oos_provenance

BPS = 10_000


@dataclass(frozen=True, slots=True)
class RecentCanonicalEconomicConfig:
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
        if self.inclusion_latency_seconds < 0:
            raise ValueError("inclusion latency must be non-negative")
        if self.decision_lead_seconds <= 0:
            raise ValueError("decision lead must be positive")
        if not 0 <= self.treasury_fee_bps < BPS:
            raise ValueError("treasury fee must be in [0, 10000)")
        if self.purge_rounds < 0:
            raise ValueError("purge_rounds must be non-negative")


@dataclass(frozen=True, slots=True)
class RecentCanonicalTrade:
    epoch: int
    side: str
    p_bull_ppm: int
    estimated_ev_wei: int
    decision_timestamp: int
    execution_timestamp: int
    projected_bull_wei: int
    projected_bear_wei: int
    final_bull_wei: int
    final_bear_wei: int
    outcome: str
    pnl_wei: int
    signal_fold: str | None
    signal_train_max_epoch: int
    projection_model_id: str
    projection_train_max_epoch: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecentCanonicalEconomicReport:
    authoritative_prediction_events: bool
    chainlink_collected: bool
    profitability_gate_eligible: bool
    config: RecentCanonicalEconomicConfig
    settled_rounds: int
    trade_count: int
    skipped_missing_signal: int
    skipped_missing_projection: int
    skipped_oos_provenance: int
    skipped_no_decision_snapshot: int
    skipped_late: int
    skipped_no_positive_ev: int
    pnl_wei: int
    capital_at_risk_wei: int
    roi_ppm: int | None
    max_drawdown_wei: int
    trades: tuple[RecentCanonicalTrade, ...]
    limitations: tuple[str, ...]

    def summary(self) -> dict[str, object]:
        return {
            "authoritative_prediction_events": self.authoritative_prediction_events,
            "chainlink_collected": self.chainlink_collected,
            "profitability_gate_eligible": self.profitability_gate_eligible,
            "config": asdict(self.config),
            "settled_rounds": self.settled_rounds,
            "trade_count": self.trade_count,
            "skipped_missing_signal": self.skipped_missing_signal,
            "skipped_missing_projection": self.skipped_missing_projection,
            "skipped_oos_provenance": self.skipped_oos_provenance,
            "skipped_no_decision_snapshot": self.skipped_no_decision_snapshot,
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
    config: RecentCanonicalEconomicConfig,
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
    record: RoundRecord,
    side: str,
    config: RecentCanonicalEconomicConfig,
) -> int:
    if record.label != side:
        return -config.stake_wei - config.bet_gas_wei
    quote = _quote(side, record.bull_amount_wei, record.bear_amount_wei, config)
    gross = gross_payout_if_win_wei(quote)
    return gross - config.stake_wei - config.bet_gas_wei - config.claim_gas_wei


def _purged(epoch: int, train_max_epoch: int | None, purge_rounds: int) -> bool:
    return (
        train_max_epoch is not None
        and train_max_epoch <= epoch - purge_rounds - 1
    )


def _max_drawdown(trades: tuple[RecentCanonicalTrade, ...]) -> int:
    equity = 0
    peak = 0
    maximum = 0
    for trade in trades:
        equity += trade.pnl_wei
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def run_recent_canonical_economic_benchmark(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    signals: Mapping[int, OosSignal],
    projections: Mapping[int, PoolProjection],
    config: RecentCanonicalEconomicConfig,
) -> RecentCanonicalEconomicReport:
    config.validate()
    validate_oos_provenance(signals.values(), purge_rounds=config.purge_rounds)
    event_index = build_event_index(events)
    timing_config = BacktestConfig(decision_lead_seconds=config.decision_lead_seconds)
    trades: list[RecentCanonicalTrade] = []
    skipped_missing_signal = 0
    skipped_missing_projection = 0
    skipped_oos = 0
    skipped_snapshot = 0
    skipped_late = 0
    skipped_no_ev = 0
    settled = 0

    for record in sorted(replay.rounds, key=lambda item: item.epoch):
        if record.label not in {"bull", "bear", "tie"}:
            continue
        settled += 1
        snapshot = build_decision_snapshot(
            replay,
            record,
            events,
            timing_config,
            event_index=event_index,
        )
        if snapshot is None:
            skipped_snapshot += 1
            continue
        signal = signals.get(record.epoch)
        if signal is None:
            skipped_missing_signal += 1
            continue
        projection = projections.get(record.epoch)
        if projection is None:
            skipped_missing_projection += 1
            continue
        projection.validate()
        if signal.epoch != record.epoch or projection.epoch != record.epoch:
            raise ValueError("recent economic signal/projection epoch mismatch")
        if signal.generated_at > snapshot.decision_timestamp:
            skipped_oos += 1
            continue
        if projection.generated_at > snapshot.decision_timestamp:
            skipped_oos += 1
            continue
        if not _purged(record.epoch, signal.train_max_epoch, config.purge_rounds):
            skipped_oos += 1
            continue
        if not _purged(record.epoch, projection.train_max_epoch, config.purge_rounds):
            skipped_oos += 1
            continue
        execution_timestamp = snapshot.decision_timestamp + config.inclusion_latency_seconds
        scheduled_lock = snapshot.decision_timestamp + config.decision_lead_seconds
        if execution_timestamp >= scheduled_lock:
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
        bull_ev = expected_value_wei(bull_quote, win_probability_ppm=signal.p_bull_ppm)
        bear_ev = expected_value_wei(
            bear_quote,
            win_probability_ppm=PPM - signal.p_bull_ppm,
        )
        side, estimated_ev = ("bull", bull_ev) if bull_ev >= bear_ev else ("bear", bear_ev)
        if estimated_ev <= config.min_expected_value_wei:
            skipped_no_ev += 1
            continue
        signal_train_max = signal.train_max_epoch
        projection_train_max = projection.train_max_epoch
        if signal_train_max is None or projection_train_max is None:
            skipped_oos += 1
            continue
        trades.append(
            RecentCanonicalTrade(
                epoch=record.epoch,
                side=side,
                p_bull_ppm=signal.p_bull_ppm,
                estimated_ev_wei=estimated_ev,
                decision_timestamp=snapshot.decision_timestamp,
                execution_timestamp=execution_timestamp,
                projected_bull_wei=projection.projected_bull_wei,
                projected_bear_wei=projection.projected_bear_wei,
                final_bull_wei=record.bull_amount_wei,
                final_bear_wei=record.bear_amount_wei,
                outcome=record.label,
                pnl_wei=_realized_pnl(record, side, config),
                signal_fold=signal.fold,
                signal_train_max_epoch=signal_train_max,
                projection_model_id=projection.model_id,
                projection_train_max_epoch=projection_train_max,
            )
        )

    trade_tuple = tuple(trades)
    pnl = sum(trade.pnl_wei for trade in trade_tuple)
    capital = len(trade_tuple) * config.stake_wei
    return RecentCanonicalEconomicReport(
        authoritative_prediction_events=True,
        chainlink_collected=False,
        profitability_gate_eligible=False,
        config=config,
        settled_rounds=settled,
        trade_count=len(trade_tuple),
        skipped_missing_signal=skipped_missing_signal,
        skipped_missing_projection=skipped_missing_projection,
        skipped_oos_provenance=skipped_oos,
        skipped_no_decision_snapshot=skipped_snapshot,
        skipped_late=skipped_late,
        skipped_no_positive_ev=skipped_no_ev,
        pnl_wei=pnl,
        capital_at_risk_wei=capital,
        roi_ppm=None if capital <= 0 else pnl * PPM // capital,
        max_drawdown_wei=_max_drawdown(trade_tuple),
        trades=trade_tuple,
        limitations=(
            "Prediction events and final pools are canonical on-chain evidence",
            "Chainlink feed events are not collected in this recent public-RPC mode",
            "treasury fee, gas, latency, and other economics are explicit scenario inputs",
            "result cannot alone pass the full-history profitability gate",
        ),
    )
