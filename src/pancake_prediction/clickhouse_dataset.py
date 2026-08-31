from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from .alignment import build_aligned_alpha_feature_row, build_aligned_alpha_inputs
from .backtest import BacktestConfig
from .baseline import ResearchFeatureRow, build_research_feature_row
from .binance import AggTrade
from .binance_archive import TimestampUnit
from .clickhouse import ClickHouseParameterizedJsonSource, load_binance_trade_window
from .features import PoolFeatureRow, build_pool_feature_rows
from .replay import ChainEvent, ReplaySnapshot
from .research_dataset import (
    BINANCE_SYMBOL_BY_MARKET,
    ChainlinkEventIndex,
    ResearchDatasetBuildResult,
    TradeTimeIndex,
)


@dataclass(frozen=True, slots=True)
class ChunkedResearchDatasetBuildResult:
    dataset: ResearchDatasetBuildResult
    chunk_span_ms: int
    chunks_loaded: int
    max_spot_chunk_rows: int
    max_perp_chunk_rows: int
    spot_query_start_ms: int | None
    perp_query_start_ms: int | None
    query_end_ms: int | None
    requested_epoch_count: int | None = None
    requested_epoch_min: int | None = None
    requested_epoch_max: int | None = None

    def as_dict(self) -> dict[str, object]:
        payload = self.dataset.as_dict()
        payload.update(
            {
                "chunk_span_ms": self.chunk_span_ms,
                "chunks_loaded": self.chunks_loaded,
                "max_spot_chunk_rows": self.max_spot_chunk_rows,
                "max_perp_chunk_rows": self.max_perp_chunk_rows,
                "spot_query_start_ms": self.spot_query_start_ms,
                "perp_query_start_ms": self.perp_query_start_ms,
                "query_end_ms": self.query_end_ms,
                "requested_epoch_count": self.requested_epoch_count,
                "requested_epoch_min": self.requested_epoch_min,
                "requested_epoch_max": self.requested_epoch_max,
            }
        )
        return payload


def _history_ms(flow_lookback_ms: int, max_price_age_ms: int) -> int:
    return max(flow_lookback_ms, max_price_age_ms)


def _group_pool_rows(
    rows: Sequence[PoolFeatureRow],
    *,
    chunk_span_ms: int,
    flow_lookback_ms: int,
) -> tuple[tuple[int, tuple[PoolFeatureRow, ...]], ...]:
    groups: dict[int, list[PoolFeatureRow]] = {}
    for row in rows:
        decision_ms = row.feature_timestamp * 1_000
        if decision_ms < flow_lookback_ms:
            continue
        chunk_start = decision_ms // chunk_span_ms * chunk_span_ms
        groups.setdefault(chunk_start, []).append(row)
    return tuple(
        (chunk_start, tuple(groups[chunk_start]))
        for chunk_start in sorted(groups)
    )


def build_chunked_clickhouse_research_dataset(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    source: ClickHouseParameterizedJsonSource,
    *,
    spot_availability_lag_ms: int,
    spot_timestamp_unit: TimestampUnit = "auto",
    perp_availability_lag_ms: int = 0,
    perp_timestamp_unit: TimestampUnit = "milliseconds",
    include_perp: bool = True,
    chunk_span_ms: int = 3_600_000,
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
    required_epochs: Collection[int] | None = None,
) -> ChunkedResearchDatasetBuildResult:
    if replay.market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {replay.market}")
    if spot_availability_lag_ms < 0 or perp_availability_lag_ms < 0:
        raise ValueError("Binance availability lag must be non-negative")
    if chunk_span_ms <= 0:
        raise ValueError("chunk_span_ms must be positive")
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

    requested_epochs = (
        None
        if required_epochs is None
        else frozenset(int(epoch) for epoch in required_epochs)
    )
    if requested_epochs is not None and any(epoch < 0 for epoch in requested_epochs):
        raise ValueError("required_epochs must be non-negative")

    config = BacktestConfig() if backtest_config is None else backtest_config
    all_pool_rows = build_pool_feature_rows(
        replay,
        events,
        config,
        feature_lead_seconds=feature_lead_seconds,
    )
    pool_rows = (
        all_pool_rows
        if requested_epochs is None
        else tuple(row for row in all_pool_rows if row.epoch in requested_epochs)
    )
    groups = _group_pool_rows(
        pool_rows,
        chunk_span_ms=chunk_span_ms,
        flow_lookback_ms=flow_lookback_ms,
    )
    chainlink_index = ChainlinkEventIndex.build(
        events,
        availability_lag_ms=chainlink_availability_lag_ms,
    )
    expected_symbol = BINANCE_SYMBOL_BY_MARKET[replay.market]

    research_rows: list[ResearchFeatureRow] = []
    skipped_market_data = len(pool_rows) - sum(len(rows) for _, rows in groups)
    max_spot_rows = 0
    max_perp_rows = 0
    campaign_spot_start: int | None = None
    campaign_perp_start: int | None = None
    campaign_end: int | None = None

    for chunk_start, rows in groups:
        chunk_end = chunk_start + chunk_span_ms
        spot_query_start = max(
            0,
            chunk_start - _history_ms(flow_lookback_ms, max_spot_age_ms),
        )
        perp_query_start = max(
            0,
            chunk_start - _history_ms(flow_lookback_ms, max_perp_age_ms),
        )
        campaign_spot_start = (
            spot_query_start
            if campaign_spot_start is None
            else min(campaign_spot_start, spot_query_start)
        )
        if include_perp:
            campaign_perp_start = (
                perp_query_start
                if campaign_perp_start is None
                else min(campaign_perp_start, perp_query_start)
            )
        campaign_end = chunk_end if campaign_end is None else max(campaign_end, chunk_end)

        spot_trades = load_binance_trade_window(
            source,
            market=replay.market,
            venue="spot",
            timestamp_unit=spot_timestamp_unit,
            availability_lag_ms=spot_availability_lag_ms,
            start_timestamp_ms=spot_query_start,
            end_timestamp_ms=chunk_end,
        )
        perp_trades: tuple[AggTrade, ...] = ()
        if include_perp:
            perp_trades = load_binance_trade_window(
                source,
                market=replay.market,
                venue="um_futures",
                timestamp_unit=perp_timestamp_unit,
                availability_lag_ms=perp_availability_lag_ms,
                start_timestamp_ms=perp_query_start,
                end_timestamp_ms=chunk_end,
            )
        max_spot_rows = max(max_spot_rows, len(spot_trades))
        max_perp_rows = max(max_perp_rows, len(perp_trades))
        spot_index = TradeTimeIndex.build(spot_trades, expected_symbol=expected_symbol)
        perp_index = TradeTimeIndex.build(perp_trades, expected_symbol=expected_symbol)

        for pool in rows:
            decision_ms = pool.feature_timestamp * 1_000
            spot_start = max(
                0,
                decision_ms - _history_ms(flow_lookback_ms, max_spot_age_ms),
            )
            perp_start = max(
                0,
                decision_ms - _history_ms(flow_lookback_ms, max_perp_age_ms),
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

    candidate_rounds = (
        len(replay.rounds)
        if requested_epochs is None
        else len(requested_epochs)
    )
    dataset = ResearchDatasetBuildResult(
        market=replay.market,
        candidate_rounds=candidate_rounds,
        pool_feature_rows=len(pool_rows),
        research_feature_rows=tuple(research_rows),
        skipped_no_pool_features=max(0, candidate_rounds - len(pool_rows)),
        skipped_no_aligned_market_data=skipped_market_data,
    )
    return ChunkedResearchDatasetBuildResult(
        dataset=dataset,
        chunk_span_ms=chunk_span_ms,
        chunks_loaded=len(groups),
        max_spot_chunk_rows=max_spot_rows,
        max_perp_chunk_rows=max_perp_rows,
        spot_query_start_ms=campaign_spot_start,
        perp_query_start_ms=campaign_perp_start,
        query_end_ms=campaign_end,
        requested_epoch_count=(
            None if requested_epochs is None else len(requested_epochs)
        ),
        requested_epoch_min=(
            None if not requested_epochs else min(requested_epochs)
        ),
        requested_epoch_max=(
            None if not requested_epochs else max(requested_epochs)
        ),
    )
