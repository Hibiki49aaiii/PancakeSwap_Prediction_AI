from __future__ import annotations

from dataclasses import dataclass, replace

from .backtest import BacktestConfig, BacktestEventIndex, build_decision_snapshot, build_event_index
from .economics import PPM
from .replay import ChainEvent, ReplaySnapshot, RoundRecord


@dataclass(frozen=True, slots=True)
class PoolFeatureRow:
    market: str
    epoch: int
    feature_timestamp: int
    scheduled_lock_timestamp: int
    bull_pool_wei: int
    bear_pool_wei: int
    bull_share_ppm: int | None
    bet_count: int
    unique_bettors: int
    last_60s_bull_wei: int
    last_60s_bear_wei: int
    prior_bull_rate_20_ppm: int | None
    prior_abs_return_12_ppm: int | None


def _pool_stats(
    events: tuple[ChainEvent, ...], *, cutoff: int
) -> tuple[int, int, int, set[str]]:
    count = 0
    bull_60 = 0
    bear_60 = 0
    bettors: set[str] = set()
    for event in events:
        if event.block_timestamp >= cutoff:
            continue
        count += 1
        sender = event.decoded.get("sender")
        if isinstance(sender, str):
            bettors.add(sender.lower())
        amount = event.decoded.get("amount")
        if not isinstance(amount, int) or amount < 0:
            continue
        if event.block_timestamp >= cutoff - 60:
            if event.event_name == "BetBull":
                bull_60 += amount
            elif event.event_name == "BetBear":
                bear_60 += amount
    return count, bull_60, bear_60, bettors


def _known_history_features(
    replay: ReplaySnapshot, *, cutoff: int, before_epoch: int
) -> tuple[int | None, int | None]:
    known = [
        record
        for record in replay.rounds
        if record.epoch < before_epoch
        and record.end_timestamp is not None
        and record.end_timestamp < cutoff
        and record.label in ("bull", "bear")
    ]
    if not known:
        return None, None
    known.sort(key=lambda record: (record.end_timestamp or 0, record.epoch))
    recent20 = known[-20:]
    bull_rate = sum(record.label == "bull" for record in recent20) * PPM // len(recent20)
    returns = [
        abs(record.close_price - record.lock_price) * PPM // abs(record.lock_price)
        for record in known[-12:]
        if record.lock_price not in (None, 0) and record.close_price is not None
    ]
    abs_return = None if not returns else sum(returns) // len(returns)
    return bull_rate, abs_return


def build_pool_feature_row(
    replay: ReplaySnapshot,
    record: RoundRecord,
    events: tuple[ChainEvent, ...],
    config: BacktestConfig,
    *,
    feature_lead_seconds: int = 20,
    event_index: BacktestEventIndex | None = None,
) -> PoolFeatureRow | None:
    if feature_lead_seconds < 0:
        raise ValueError("feature_lead_seconds must be non-negative")
    index = build_event_index(events) if event_index is None else event_index
    feature_config = replace(config, decision_lead_seconds=feature_lead_seconds)
    snapshot = build_decision_snapshot(
        replay, record, events, feature_config, event_index=index
    )
    if snapshot is None:
        return None
    epoch_bets = index.bets_by_epoch.get(record.epoch, ())
    count, bull_60, bear_60, bettors = _pool_stats(epoch_bets, cutoff=snapshot.decision_timestamp)
    prior_bull, prior_abs_return = _known_history_features(
        replay, cutoff=snapshot.decision_timestamp, before_epoch=record.epoch
    )
    total = snapshot.total_observed_wei
    return PoolFeatureRow(
        market=replay.market,
        epoch=record.epoch,
        feature_timestamp=snapshot.decision_timestamp,
        scheduled_lock_timestamp=snapshot.scheduled_lock_timestamp,
        bull_pool_wei=snapshot.bull_observed_wei,
        bear_pool_wei=snapshot.bear_observed_wei,
        bull_share_ppm=(None if total == 0 else snapshot.bull_observed_wei * PPM // total),
        bet_count=count,
        unique_bettors=len(bettors),
        last_60s_bull_wei=bull_60,
        last_60s_bear_wei=bear_60,
        prior_bull_rate_20_ppm=prior_bull,
        prior_abs_return_12_ppm=prior_abs_return,
    )


def build_pool_feature_rows(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    config: BacktestConfig,
    *,
    feature_lead_seconds: int = 20,
) -> tuple[PoolFeatureRow, ...]:
    index = build_event_index(events)
    rows: list[PoolFeatureRow] = []
    for record in replay.rounds:
        row = build_pool_feature_row(
            replay,
            record,
            events,
            config,
            feature_lead_seconds=feature_lead_seconds,
            event_index=index,
        )
        if row is not None:
            rows.append(row)
    return tuple(rows)
