from __future__ import annotations

from dataclasses import dataclass, replace

from .alpha import relative_gap_ppm
from .baseline import ResearchFeatureRow
from .binance import AggTrade, aggregate_order_flow
from .binance_archive import TimestampUnit
from .clickhouse import ClickHouseParameterizedJsonSource, load_binance_trade_window
from .research_dataset import TradeTimeIndex

MICROSTRUCTURE_HORIZONS_MS = (5_000, 20_000, 60_000)


@dataclass(frozen=True, slots=True)
class LegacyMicrostructureBuildResult:
    rows: tuple[ResearchFeatureRow, ...]
    chunks_loaded: int
    max_spot_chunk_rows: int
    max_perp_chunk_rows: int

    def as_dict(self) -> dict[str, object]:
        return {
            "research_feature_rows": len(self.rows),
            "chunks_loaded": self.chunks_loaded,
            "max_spot_chunk_rows": self.max_spot_chunk_rows,
            "max_perp_chunk_rows": self.max_perp_chunk_rows,
        }


def _available_window(
    index: TradeTimeIndex,
    *,
    decision_ms: int,
    horizon_ms: int,
) -> tuple[AggTrade, ...]:
    start = max(0, decision_ms - horizon_ms)
    return tuple(
        trade
        for trade in index.window(
            start_timestamp_ms=start,
            end_timestamp_ms=decision_ms,
        )
        if trade.event_timestamp_ms < decision_ms
    )


def _horizon_values(
    index: TradeTimeIndex,
    *,
    prefix: str,
    decision_ms: int,
    horizon_ms: int,
) -> dict[str, float | None]:
    trades = _available_window(
        index,
        decision_ms=decision_ms,
        horizon_ms=horizon_ms,
    )
    seconds = horizon_ms // 1_000
    if not trades:
        return {
            f"{prefix}_return_{seconds}s_ppm": None,
            f"{prefix}_flow_imbalance_{seconds}s_ppm": None,
            f"{prefix}_trade_count_{seconds}s": 0.0,
        }
    flow = aggregate_order_flow(
        trades,
        start_timestamp_ms=max(0, decision_ms - horizon_ms),
        end_timestamp_ms=decision_ms,
    )
    price_return: float | None = None
    if len(trades) >= 2:
        price_return = float(relative_gap_ppm(trades[-1].price_e8, trades[0].price_e8))
    return {
        f"{prefix}_return_{seconds}s_ppm": price_return,
        f"{prefix}_flow_imbalance_{seconds}s_ppm": (
            None if flow.imbalance_ppm is None else float(flow.imbalance_ppm)
        ),
        f"{prefix}_trade_count_{seconds}s": float(flow.trade_count),
    }


def _enrich_row(
    row: ResearchFeatureRow,
    spot_index: TradeTimeIndex,
    perp_index: TradeTimeIndex,
    *,
    include_perp: bool,
) -> ResearchFeatureRow:
    values = dict(row.values)
    for horizon_ms in MICROSTRUCTURE_HORIZONS_MS:
        values.update(
            _horizon_values(
                spot_index,
                prefix="spot",
                decision_ms=row.decision_timestamp_ms,
                horizon_ms=horizon_ms,
            )
        )
        if include_perp:
            values.update(
                _horizon_values(
                    perp_index,
                    prefix="perp",
                    decision_ms=row.decision_timestamp_ms,
                    horizon_ms=horizon_ms,
                )
            )
        else:
            seconds = horizon_ms // 1_000
            values[f"perp_return_{seconds}s_ppm"] = None
            values[f"perp_flow_imbalance_{seconds}s_ppm"] = None
            values[f"perp_trade_count_{seconds}s"] = None
    return replace(row, values=values)


def enrich_legacy_microstructure_rows(
    rows: tuple[ResearchFeatureRow, ...],
    source: ClickHouseParameterizedJsonSource,
    *,
    spot_timestamp_unit: TimestampUnit,
    spot_availability_lag_ms: int,
    perp_timestamp_unit: TimestampUnit = "milliseconds",
    perp_availability_lag_ms: int = 0,
    include_perp: bool = True,
    chunk_span_ms: int = 3_600_000,
) -> LegacyMicrostructureBuildResult:
    if spot_availability_lag_ms < 0 or perp_availability_lag_ms < 0:
        raise ValueError("Binance availability lag must be non-negative")
    if chunk_span_ms <= 0:
        raise ValueError("chunk_span_ms must be positive")
    if not rows:
        return LegacyMicrostructureBuildResult((), 0, 0, 0)

    groups: dict[int, list[ResearchFeatureRow]] = {}
    for row in rows:
        chunk_start = row.decision_timestamp_ms // chunk_span_ms * chunk_span_ms
        groups.setdefault(chunk_start, []).append(row)

    enriched: list[ResearchFeatureRow] = []
    max_spot_rows = 0
    max_perp_rows = 0
    max_horizon = max(MICROSTRUCTURE_HORIZONS_MS)
    for chunk_start in sorted(groups):
        chunk_end = chunk_start + chunk_span_ms
        query_start = max(0, chunk_start - max_horizon)
        spot_trades = load_binance_trade_window(
            source,
            market="BNBUSD",
            venue="spot",
            timestamp_unit=spot_timestamp_unit,
            availability_lag_ms=spot_availability_lag_ms,
            start_timestamp_ms=query_start,
            end_timestamp_ms=chunk_end,
        )
        perp_trades: tuple[AggTrade, ...] = ()
        if include_perp:
            perp_trades = load_binance_trade_window(
                source,
                market="BNBUSD",
                venue="um_futures",
                timestamp_unit=perp_timestamp_unit,
                availability_lag_ms=perp_availability_lag_ms,
                start_timestamp_ms=query_start,
                end_timestamp_ms=chunk_end,
            )
        max_spot_rows = max(max_spot_rows, len(spot_trades))
        max_perp_rows = max(max_perp_rows, len(perp_trades))
        spot_index = TradeTimeIndex.build(spot_trades, expected_symbol="BNBUSDT")
        perp_index = TradeTimeIndex.build(perp_trades, expected_symbol="BNBUSDT")
        for row in groups[chunk_start]:
            enriched.append(
                _enrich_row(
                    row,
                    spot_index,
                    perp_index,
                    include_perp=include_perp,
                )
            )

    enriched.sort(key=lambda row: row.epoch)
    return LegacyMicrostructureBuildResult(
        rows=tuple(enriched),
        chunks_loaded=len(groups),
        max_spot_chunk_rows=max_spot_rows,
        max_perp_chunk_rows=max_perp_rows,
    )
