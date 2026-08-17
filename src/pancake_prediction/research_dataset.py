from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .alignment import build_aligned_alpha_feature_row, build_aligned_alpha_inputs
from .backtest import BacktestConfig
from .baseline import ResearchFeatureRow, build_research_feature_row
from .binance import AggTrade
from .features import build_pool_feature_rows
from .replay import ChainEvent, ReplaySnapshot

BINANCE_SYMBOL_BY_MARKET = {
    "BNBUSD": "BNBUSDT",
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
}


@dataclass(frozen=True, slots=True)
class TradeTimeIndex:
    trades: tuple[AggTrade, ...]
    timestamps_ms: tuple[int, ...]

    @classmethod
    def build(
        cls,
        trades: Iterable[AggTrade],
        *,
        expected_symbol: str,
    ) -> TradeTimeIndex:
        ordered = tuple(
            sorted(
                trades,
                key=lambda trade: (
                    trade.trade_timestamp_ms,
                    trade.event_timestamp_ms,
                    trade.aggregate_trade_id,
                ),
            )
        )
        expected = expected_symbol.upper()
        seen_ids: set[int] = set()
        for trade in ordered:
            if trade.symbol.upper() != expected:
                raise ValueError(
                    f"unexpected Binance symbol {trade.symbol!r}; expected {expected!r}"
                )
            if trade.aggregate_trade_id in seen_ids:
                raise ValueError(f"duplicate aggregate trade id: {trade.aggregate_trade_id}")
            seen_ids.add(trade.aggregate_trade_id)
        return cls(
            trades=ordered,
            timestamps_ms=tuple(trade.trade_timestamp_ms for trade in ordered),
        )

    def window(self, *, start_timestamp_ms: int, end_timestamp_ms: int) -> tuple[AggTrade, ...]:
        if start_timestamp_ms < 0 or end_timestamp_ms <= start_timestamp_ms:
            raise ValueError("invalid trade index window")
        left = bisect_left(self.timestamps_ms, start_timestamp_ms)
        right = bisect_left(self.timestamps_ms, end_timestamp_ms)
        return self.trades[left:right]


@dataclass(frozen=True, slots=True)
class ChainlinkEventIndex:
    events: tuple[ChainEvent, ...]
    available_at_ms: tuple[int, ...]

    @classmethod
    def build(cls, events: Iterable[ChainEvent]) -> ChainlinkEventIndex:
        rows: list[tuple[int, ChainEvent]] = []
        for event in events:
            if event.event_name != "AnswerUpdated":
                continue
            current = event.decoded.get("current")
            updated_at = event.decoded.get("updatedAt")
            if not isinstance(current, int) or current <= 0:
                continue
            if not isinstance(updated_at, int) or updated_at < 0:
                continue
            available = max(updated_at * 1_000, event.block_timestamp * 1_000)
            rows.append((available, event))
        rows.sort(
            key=lambda item: (
                item[0],
                item[1].block_number,
                item[1].tx_index,
                item[1].log_index,
            )
        )
        return cls(
            events=tuple(event for _, event in rows),
            available_at_ms=tuple(available for available, _ in rows),
        )

    def recent_before(
        self,
        decision_timestamp_ms: int,
        *,
        limit: int,
    ) -> tuple[ChainEvent, ...]:
        if decision_timestamp_ms < 0:
            raise ValueError("decision timestamp must be non-negative")
        if limit < 2:
            raise ValueError("Chainlink history limit must be at least 2")
        end = bisect_left(self.available_at_ms, decision_timestamp_ms)
        start = max(0, end - limit)
        return self.events[start:end]


@dataclass(frozen=True, slots=True)
class ResearchDatasetBuildResult:
    market: str
    candidate_rounds: int
    pool_feature_rows: int
    research_feature_rows: tuple[ResearchFeatureRow, ...]
    skipped_no_pool_features: int
    skipped_no_aligned_market_data: int

    @property
    def row_count(self) -> int:
        return len(self.research_feature_rows)

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "candidate_rounds": self.candidate_rounds,
            "pool_feature_rows": self.pool_feature_rows,
            "research_feature_rows": self.row_count,
            "skipped_no_pool_features": self.skipped_no_pool_features,
            "skipped_no_aligned_market_data": self.skipped_no_aligned_market_data,
        }


def _trade_slice_start(
    decision_timestamp_ms: int,
    *,
    flow_lookback_ms: int,
    max_price_age_ms: int,
) -> int:
    history_ms = max(flow_lookback_ms, max_price_age_ms)
    return max(0, decision_timestamp_ms - history_ms)


def build_research_dataset(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    spot_trades: Sequence[AggTrade],
    *,
    perp_trades: Sequence[AggTrade] = (),
    backtest_config: BacktestConfig | None = None,
    feature_lead_seconds: int = 20,
    flow_lookback_ms: int = 60_000,
    max_spot_age_ms: int = 5_000,
    max_perp_age_ms: int = 5_000,
    max_chainlink_age_ms: int | None = None,
    chainlink_availability_lag_ms: int = 0,
    oracle_history_updates: int = 512,
    oracle_hazard_horizon_ms: int = 5_000,
    oracle_hazard_min_intervals: int = 8,
) -> ResearchDatasetBuildResult:
    if replay.market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {replay.market}")
    if flow_lookback_ms <= 0:
        raise ValueError("flow_lookback_ms must be positive")
    if max_spot_age_ms < 0 or max_perp_age_ms < 0:
        raise ValueError("Binance max age values must be non-negative")
    if max_chainlink_age_ms is not None and max_chainlink_age_ms < 0:
        raise ValueError("max_chainlink_age_ms must be non-negative")
    if chainlink_availability_lag_ms < 0:
        raise ValueError("chainlink_availability_lag_ms must be non-negative")
    if oracle_history_updates < oracle_hazard_min_intervals + 1:
        raise ValueError("oracle_history_updates must cover hazard minimum intervals")

    config = BacktestConfig() if backtest_config is None else backtest_config
    expected_symbol = BINANCE_SYMBOL_BY_MARKET[replay.market]
    spot_index = TradeTimeIndex.build(spot_trades, expected_symbol=expected_symbol)
    perp_index = TradeTimeIndex.build(perp_trades, expected_symbol=expected_symbol)
    chainlink_index = ChainlinkEventIndex.build(events)
    pool_rows = build_pool_feature_rows(
        replay,
        events,
        config,
        feature_lead_seconds=feature_lead_seconds,
    )

    research_rows: list[ResearchFeatureRow] = []
    skipped_market_data = 0
    for pool in pool_rows:
        decision_ms = pool.feature_timestamp * 1_000
        if decision_ms < flow_lookback_ms:
            skipped_market_data += 1
            continue
        spot_start = _trade_slice_start(
            decision_ms,
            flow_lookback_ms=flow_lookback_ms,
            max_price_age_ms=max_spot_age_ms,
        )
        perp_start = _trade_slice_start(
            decision_ms,
            flow_lookback_ms=flow_lookback_ms,
            max_price_age_ms=max_perp_age_ms,
        )
        spot_window = spot_index.window(
            start_timestamp_ms=spot_start,
            end_timestamp_ms=decision_ms,
        )
        perp_window = perp_index.window(
            start_timestamp_ms=perp_start,
            end_timestamp_ms=decision_ms,
        )
        chainlink_window = chainlink_index.recent_before(
            decision_ms,
            limit=oracle_history_updates,
        )
        aligned = build_aligned_alpha_inputs(
            decision_timestamp_ms=decision_ms,
            chainlink_events=chainlink_window,
            spot_trades=spot_window,
            perp_trades=perp_window,
            flow_lookback_ms=flow_lookback_ms,
            max_spot_age_ms=max_spot_age_ms,
            max_perp_age_ms=max_perp_age_ms,
            max_chainlink_age_ms=max_chainlink_age_ms,
            chainlink_availability_lag_ms=chainlink_availability_lag_ms,
        )
        if aligned is None:
            skipped_market_data += 1
            continue
        alpha = build_aligned_alpha_feature_row(
            market=replay.market,
            epoch=pool.epoch,
            aligned=aligned,
            oracle_hazard_horizon_ms=oracle_hazard_horizon_ms,
            oracle_hazard_min_intervals=oracle_hazard_min_intervals,
        )
        research_rows.append(build_research_feature_row(alpha=alpha, pool=pool))

    return ResearchDatasetBuildResult(
        market=replay.market,
        candidate_rounds=len(replay.rounds),
        pool_feature_rows=len(pool_rows),
        research_feature_rows=tuple(research_rows),
        skipped_no_pool_features=len(replay.rounds) - len(pool_rows),
        skipped_no_aligned_market_data=skipped_market_data,
    )
