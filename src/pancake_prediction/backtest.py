from __future__ import annotations

from dataclasses import asdict, dataclass

from .economics import PPM, ParimutuelQuote, expected_value_wei, gross_payout_if_win_wei
from .replay import ChainEvent, ReplaySnapshot, RoundRecord

BPS = 10_000


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    stake_wei: int = 10**15
    initial_interval_seconds: int = 300
    initial_treasury_fee_bps: int = 300
    initial_buffer_seconds: int = 30
    decision_lead_seconds: int = 20
    inclusion_latency_seconds: int = 3
    bet_gas_wei: int = 0
    claim_gas_wei: int = 0
    min_expected_value_wei: int = 0
    require_pool_projection: bool = True

    def validate(self) -> None:
        if self.stake_wei <= 0:
            raise ValueError("stake_wei must be positive")
        if self.initial_interval_seconds <= 0:
            raise ValueError("initial_interval_seconds must be positive")
        if not 0 <= self.initial_treasury_fee_bps < BPS:
            raise ValueError("initial_treasury_fee_bps must be in [0, 10000)")
        if self.initial_buffer_seconds < 0:
            raise ValueError("initial_buffer_seconds must be non-negative")
        if self.decision_lead_seconds < 0 or self.inclusion_latency_seconds < 0:
            raise ValueError("timing values must be non-negative")
        if self.bet_gas_wei < 0 or self.claim_gas_wei < 0:
            raise ValueError("gas costs must be non-negative")


@dataclass(frozen=True, slots=True)
class BacktestSignal:
    epoch: int
    p_bull_ppm: int
    generated_at: int
    model_id: str
    train_max_epoch: int | None = None

    def validate(self) -> None:
        if self.epoch < 0 or self.generated_at < 0:
            raise ValueError("signal epoch/timestamp must be non-negative")
        if not 0 <= self.p_bull_ppm <= PPM:
            raise ValueError("p_bull_ppm must be in [0, 1_000_000]")


@dataclass(frozen=True, slots=True)
class PoolProjection:
    epoch: int
    generated_at: int
    projected_bull_wei: int
    projected_bear_wei: int
    model_id: str

    def validate(self) -> None:
        if self.epoch < 0 or self.generated_at < 0:
            raise ValueError("projection epoch/timestamp must be non-negative")
        if self.projected_bull_wei < 0 or self.projected_bear_wei < 0:
            raise ValueError("projected pools must be non-negative")


@dataclass(frozen=True, slots=True)
class BacktestEventIndex:
    bets_by_epoch: dict[int, tuple[ChainEvent, ...]]
    protocol_events: tuple[ChainEvent, ...]


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    market: str
    epoch: int
    decision_timestamp: int
    scheduled_lock_timestamp: int
    interval_seconds: int
    treasury_fee_bps: int
    buffer_seconds: int
    bull_observed_wei: int
    bear_observed_wei: int

    @property
    def total_observed_wei(self) -> int:
        return self.bull_observed_wei + self.bear_observed_wei


@dataclass(frozen=True, slots=True)
class TradeResult:
    epoch: int
    side: str
    p_bull_ppm: int
    estimated_ev_wei: int
    decision_timestamp: int
    execution_timestamp: int
    scheduled_lock_timestamp: int
    signal_model_id: str
    projection_model_id: str
    observed_bull_wei: int
    observed_bear_wei: int
    projected_bull_wei: int
    projected_bear_wei: int
    final_bull_wei: int
    final_bear_wei: int
    treasury_fee_bps: int
    outcome: str
    pnl_wei: int


@dataclass(frozen=True, slots=True)
class BacktestReport:
    market: str
    replay_digest: str
    config: BacktestConfig
    settled_rounds: int
    trades: tuple[TradeResult, ...]
    skipped_no_signal: int
    skipped_no_projection: int
    skipped_no_positive_ev: int
    skipped_late: int
    skipped_integrity: int

    @property
    def pnl_wei(self) -> int:
        return sum(trade.pnl_wei for trade in self.trades)

    @property
    def stake_deployed_wei(self) -> int:
        return len(self.trades) * self.config.stake_wei

    @property
    def roi_ppm(self) -> int | None:
        if self.stake_deployed_wei == 0:
            return None
        return self.pnl_wei * PPM // self.stake_deployed_wei

    @property
    def max_drawdown_wei(self) -> int:
        equity = 0
        peak = 0
        drawdown = 0
        for trade in self.trades:
            equity += trade.pnl_wei
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
        return drawdown

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "replay_digest": self.replay_digest,
            "config": asdict(self.config),
            "settled_rounds": self.settled_rounds,
            "trade_count": len(self.trades),
            "pnl_wei": self.pnl_wei,
            "stake_deployed_wei": self.stake_deployed_wei,
            "roi_ppm": self.roi_ppm,
            "max_drawdown_wei": self.max_drawdown_wei,
            "skipped_no_signal": self.skipped_no_signal,
            "skipped_no_projection": self.skipped_no_projection,
            "skipped_no_positive_ev": self.skipped_no_positive_ev,
            "skipped_late": self.skipped_late,
            "skipped_integrity": self.skipped_integrity,
            "trades": [asdict(trade) for trade in self.trades],
        }


def build_event_index(events: tuple[ChainEvent, ...]) -> BacktestEventIndex:
    bets: dict[int, list[ChainEvent]] = {}
    protocol: list[ChainEvent] = []
    for event in events:
        if event.event_name in ("BetBull", "BetBear"):
            epoch = event.decoded.get("epoch")
            if isinstance(epoch, int):
                bets.setdefault(epoch, []).append(event)
        elif event.event_name in ("NewBufferAndIntervalSeconds", "NewTreasuryFee"):
            protocol.append(event)
    frozen_bets = {
        epoch: tuple(
            sorted(rows, key=lambda item: (item.block_number, item.tx_index, item.log_index))
        )
        for epoch, rows in bets.items()
    }
    protocol.sort(key=lambda item: (item.block_number, item.tx_index, item.log_index))
    return BacktestEventIndex(frozen_bets, tuple(protocol))


def _known_protocol_state(
    events: tuple[ChainEvent, ...],
    *,
    before_block: int,
    config: BacktestConfig,
) -> tuple[int, int, int]:
    interval = config.initial_interval_seconds
    fee = config.initial_treasury_fee_bps
    buffer = config.initial_buffer_seconds
    for event in events:
        if event.block_number >= before_block:
            break
        if event.event_name == "NewBufferAndIntervalSeconds":
            interval_value = event.decoded.get("intervalSeconds")
            buffer_value = event.decoded.get("bufferSeconds")
            if isinstance(interval_value, int) and interval_value > 0:
                interval = interval_value
            if isinstance(buffer_value, int) and buffer_value >= 0:
                buffer = buffer_value
        elif event.event_name == "NewTreasuryFee":
            fee_value = event.decoded.get("treasuryFee")
            if isinstance(fee_value, int) and 0 <= fee_value < BPS:
                fee = fee_value
    return interval, fee, buffer


def _observed_pool(events: tuple[ChainEvent, ...], *, cutoff: int) -> tuple[int, int]:
    bull = 0
    bear = 0
    for event in events:
        if event.block_timestamp >= cutoff:
            continue
        amount = event.decoded.get("amount")
        if not isinstance(amount, int) or amount < 0:
            continue
        if event.event_name == "BetBull":
            bull += amount
        elif event.event_name == "BetBear":
            bear += amount
    return bull, bear


def build_decision_snapshot(
    replay: ReplaySnapshot,
    round_record: RoundRecord,
    events: tuple[ChainEvent, ...],
    config: BacktestConfig,
    *,
    event_index: BacktestEventIndex | None = None,
) -> DecisionSnapshot | None:
    config.validate()
    if round_record.start_timestamp is None or round_record.start_block is None:
        return None
    index = build_event_index(events) if event_index is None else event_index
    interval, fee, buffer = _known_protocol_state(
        index.protocol_events,
        before_block=round_record.start_block,
        config=config,
    )
    scheduled_lock = round_record.start_timestamp + interval
    decision_timestamp = scheduled_lock - config.decision_lead_seconds
    if decision_timestamp <= round_record.start_timestamp:
        return None
    bull, bear = _observed_pool(
        index.bets_by_epoch.get(round_record.epoch, ()), cutoff=decision_timestamp
    )
    return DecisionSnapshot(
        market=replay.market,
        epoch=round_record.epoch,
        decision_timestamp=decision_timestamp,
        scheduled_lock_timestamp=scheduled_lock,
        interval_seconds=interval,
        treasury_fee_bps=fee,
        buffer_seconds=buffer,
        bull_observed_wei=bull,
        bear_observed_wei=bear,
    )


def _quote(
    *,
    side: str,
    bull_pool_wei: int,
    bear_pool_wei: int,
    fee_bps: int,
    config: BacktestConfig,
) -> ParimutuelQuote:
    side_pool = bull_pool_wei if side == "bull" else bear_pool_wei
    opposing = bear_pool_wei if side == "bull" else bull_pool_wei
    return ParimutuelQuote(
        side=side,
        side_pool_wei=side_pool,
        opposing_pool_wei=opposing,
        stake_wei=config.stake_wei,
        fee_bps=fee_bps,
        bet_gas_wei=config.bet_gas_wei,
        claim_gas_wei=config.claim_gas_wei,
    )


def _validate_projection(snapshot: DecisionSnapshot, projection: PoolProjection) -> None:
    projection.validate()
    if projection.epoch != snapshot.epoch:
        raise ValueError("projection epoch does not match decision snapshot")
    if projection.generated_at > snapshot.decision_timestamp:
        raise ValueError("pool projection was generated after the decision cutoff")
    if projection.projected_bull_wei < snapshot.bull_observed_wei:
        raise ValueError("projected bull pool cannot be below already-observed bull pool")
    if projection.projected_bear_wei < snapshot.bear_observed_wei:
        raise ValueError("projected bear pool cannot be below already-observed bear pool")


def _validate_signal(snapshot: DecisionSnapshot, signal: BacktestSignal) -> None:
    signal.validate()
    if signal.epoch != snapshot.epoch:
        raise ValueError("signal epoch does not match decision snapshot")
    if signal.generated_at > snapshot.decision_timestamp:
        raise ValueError("signal was generated after the decision cutoff")


def _round_reward_integrity(record: RoundRecord, fee_bps: int) -> bool:
    if record.label not in ("bull", "bear"):
        return True
    if (
        record.reward_base_cal_amount_wei is None
        or record.reward_amount_wei is None
        or record.treasury_amount_wei is None
    ):
        return True
    expected_base = record.bull_amount_wei if record.label == "bull" else record.bear_amount_wei
    expected_treasury = record.total_amount_wei * fee_bps // BPS
    expected_reward = record.total_amount_wei - expected_treasury
    return (
        record.reward_base_cal_amount_wei == expected_base
        and record.treasury_amount_wei == expected_treasury
        and record.reward_amount_wei == expected_reward
    )


def _realized_pnl(
    record: RoundRecord,
    *,
    side: str,
    fee_bps: int,
    config: BacktestConfig,
) -> int:
    if record.label != side:
        return -config.stake_wei - config.bet_gas_wei
    quote = _quote(
        side=side,
        bull_pool_wei=record.bull_amount_wei,
        bear_pool_wei=record.bear_amount_wei,
        fee_bps=fee_bps,
        config=config,
    )
    gross = gross_payout_if_win_wei(quote)
    return gross - config.stake_wei - config.bet_gas_wei - config.claim_gas_wei


def run_backtest(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    signals: dict[int, BacktestSignal],
    projections: dict[int, PoolProjection],
    config: BacktestConfig,
) -> BacktestReport:
    config.validate()
    event_index = build_event_index(events)
    trades: list[TradeResult] = []
    skipped_no_signal = 0
    skipped_no_projection = 0
    skipped_no_positive_ev = 0
    skipped_late = 0
    skipped_integrity = 0
    settled_rounds = 0

    for record in replay.rounds:
        if record.label not in ("bull", "bear", "tie"):
            continue
        settled_rounds += 1
        snapshot = build_decision_snapshot(
            replay, record, events, config, event_index=event_index
        )
        if snapshot is None:
            skipped_integrity += 1
            continue
        if not _round_reward_integrity(record, snapshot.treasury_fee_bps):
            skipped_integrity += 1
            continue
        signal = signals.get(record.epoch)
        if signal is None:
            skipped_no_signal += 1
            continue
        _validate_signal(snapshot, signal)

        projection = projections.get(record.epoch)
        if projection is None:
            if config.require_pool_projection:
                skipped_no_projection += 1
                continue
            projection = PoolProjection(
                epoch=record.epoch,
                generated_at=snapshot.decision_timestamp,
                projected_bull_wei=snapshot.bull_observed_wei,
                projected_bear_wei=snapshot.bear_observed_wei,
                model_id="observed-hold",
            )
        _validate_projection(snapshot, projection)

        execution_timestamp = snapshot.decision_timestamp + config.inclusion_latency_seconds
        if execution_timestamp >= snapshot.scheduled_lock_timestamp:
            skipped_late += 1
            continue

        bull_quote = _quote(
            side="bull",
            bull_pool_wei=projection.projected_bull_wei,
            bear_pool_wei=projection.projected_bear_wei,
            fee_bps=snapshot.treasury_fee_bps,
            config=config,
        )
        bear_quote = _quote(
            side="bear",
            bull_pool_wei=projection.projected_bull_wei,
            bear_pool_wei=projection.projected_bear_wei,
            fee_bps=snapshot.treasury_fee_bps,
            config=config,
        )
        bull_ev = expected_value_wei(bull_quote, win_probability_ppm=signal.p_bull_ppm)
        bear_ev = expected_value_wei(bear_quote, win_probability_ppm=PPM - signal.p_bull_ppm)
        side, estimated_ev = ("bull", bull_ev) if bull_ev >= bear_ev else ("bear", bear_ev)
        if estimated_ev <= config.min_expected_value_wei:
            skipped_no_positive_ev += 1
            continue

        trades.append(
            TradeResult(
                epoch=record.epoch,
                side=side,
                p_bull_ppm=signal.p_bull_ppm,
                estimated_ev_wei=estimated_ev,
                decision_timestamp=snapshot.decision_timestamp,
                execution_timestamp=execution_timestamp,
                scheduled_lock_timestamp=snapshot.scheduled_lock_timestamp,
                signal_model_id=signal.model_id,
                projection_model_id=projection.model_id,
                observed_bull_wei=snapshot.bull_observed_wei,
                observed_bear_wei=snapshot.bear_observed_wei,
                projected_bull_wei=projection.projected_bull_wei,
                projected_bear_wei=projection.projected_bear_wei,
                final_bull_wei=record.bull_amount_wei,
                final_bear_wei=record.bear_amount_wei,
                treasury_fee_bps=snapshot.treasury_fee_bps,
                outcome=record.label,
                pnl_wei=_realized_pnl(
                    record, side=side, fee_bps=snapshot.treasury_fee_bps, config=config
                ),
            )
        )

    return BacktestReport(
        market=replay.market,
        replay_digest=replay.output_digest,
        config=config,
        settled_rounds=settled_rounds,
        trades=tuple(trades),
        skipped_no_signal=skipped_no_signal,
        skipped_no_projection=skipped_no_projection,
        skipped_no_positive_ev=skipped_no_positive_ev,
        skipped_late=skipped_late,
        skipped_integrity=skipped_integrity,
    )
