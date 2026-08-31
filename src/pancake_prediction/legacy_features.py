from __future__ import annotations

from dataclasses import dataclass

from .alpha import relative_gap_ppm
from .baseline import ResearchFeatureRow
from .binance import AggTrade, aggregate_order_flow
from .binance_archive import TimestampUnit
from .clickhouse import ClickHouseParameterizedJsonSource, load_binance_trade_window
from .legacy_rounds import LegacyRoundRecord
from .research_dataset import TradeTimeIndex

PPM = 1_000_000


@dataclass(frozen=True, slots=True)
class LegacyFeatureBuildResult:
    rows: tuple[ResearchFeatureRow, ...]
    candidate_rounds: int
    skipped_invalid_round: int
    skipped_no_spot: int
    chunks_loaded: int
    max_spot_chunk_rows: int
    max_perp_chunk_rows: int
    spot_query_start_ms: int | None
    perp_query_start_ms: int | None
    query_end_ms: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "research_feature_rows": len(self.rows),
            "candidate_rounds": self.candidate_rounds,
            "skipped_invalid_round": self.skipped_invalid_round,
            "skipped_no_spot": self.skipped_no_spot,
            "chunks_loaded": self.chunks_loaded,
            "max_spot_chunk_rows": self.max_spot_chunk_rows,
            "max_perp_chunk_rows": self.max_perp_chunk_rows,
            "spot_query_start_ms": self.spot_query_start_ms,
            "perp_query_start_ms": self.perp_query_start_ms,
            "query_end_ms": self.query_end_ms,
        }


def _decision_timestamp_ms(record: LegacyRoundRecord, *, feature_lead_seconds: int) -> int:
    return record.lock_timestamp * 1_000 - feature_lead_seconds * 1_000


def _history_features(
    rounds: tuple[LegacyRoundRecord, ...],
    *,
    target_epoch: int,
    decision_timestamp_ms: int,
) -> tuple[int | None, int | None]:
    known = [
        record
        for record in rounds
        if record.epoch < target_epoch
        and record.oracle_called
        and record.label in {"bull", "bear"}
        and record.close_timestamp * 1_000 < decision_timestamp_ms
    ]
    known.sort(key=lambda record: (record.close_timestamp, record.epoch))
    if not known:
        return None, None
    recent20 = known[-20:]
    bull_rate = sum(record.label == "bull" for record in recent20) * PPM // len(recent20)
    returns = [
        abs(record.close_price_e8 - record.lock_price_e8) * PPM // record.lock_price_e8
        for record in known[-12:]
        if record.lock_price_e8 > 0
    ]
    abs_return = None if not returns else sum(returns) // len(returns)
    return bull_rate, abs_return


def _group_rounds(
    rounds: tuple[LegacyRoundRecord, ...],
    *,
    feature_lead_seconds: int,
    chunk_span_ms: int,
) -> tuple[tuple[int, tuple[LegacyRoundRecord, ...]], ...]:
    groups: dict[int, list[LegacyRoundRecord]] = {}
    for record in rounds:
        decision_ms = _decision_timestamp_ms(
            record,
            feature_lead_seconds=feature_lead_seconds,
        )
        if decision_ms <= record.start_timestamp * 1_000:
            continue
        chunk_start = decision_ms // chunk_span_ms * chunk_span_ms
        groups.setdefault(chunk_start, []).append(record)
    return tuple(
        (chunk_start, tuple(groups[chunk_start]))
        for chunk_start in sorted(groups)
    )


def _window_features(
    index: TradeTimeIndex,
    *,
    decision_ms: int,
    flow_lookback_ms: int,
    max_price_age_ms: int,
) -> tuple[int | None, int | None]:
    start = max(0, decision_ms - max(flow_lookback_ms, max_price_age_ms))
    trades = index.window(start_timestamp_ms=start, end_timestamp_ms=decision_ms)
    flow = aggregate_order_flow(
        trades,
        start_timestamp_ms=max(0, decision_ms - flow_lookback_ms),
        end_timestamp_ms=decision_ms,
    )
    price_window = aggregate_order_flow(
        trades,
        start_timestamp_ms=max(0, decision_ms - max_price_age_ms),
        end_timestamp_ms=decision_ms,
    )
    return price_window.last_price_e8, flow.imbalance_ppm


def _feature_row(
    rounds: tuple[LegacyRoundRecord, ...],
    record: LegacyRoundRecord,
    spot_index: TradeTimeIndex,
    perp_index: TradeTimeIndex,
    *,
    feature_lead_seconds: int,
    flow_lookback_ms: int,
    max_spot_age_ms: int,
    max_perp_age_ms: int,
    include_perp: bool,
) -> ResearchFeatureRow | None:
    decision_ms = _decision_timestamp_ms(
        record,
        feature_lead_seconds=feature_lead_seconds,
    )
    spot_price, spot_flow = _window_features(
        spot_index,
        decision_ms=decision_ms,
        flow_lookback_ms=flow_lookback_ms,
        max_price_age_ms=max_spot_age_ms,
    )
    if spot_price is None:
        return None
    perp_price: int | None = None
    perp_flow: int | None = None
    if include_perp:
        perp_price, perp_flow = _window_features(
            perp_index,
            decision_ms=decision_ms,
            flow_lookback_ms=flow_lookback_ms,
            max_price_age_ms=max_perp_age_ms,
        )
    prior_bull, prior_abs_return = _history_features(
        rounds,
        target_epoch=record.epoch,
        decision_timestamp_ms=decision_ms,
    )
    values: dict[str, float | None] = {
        "oracle_age_ms": None,
        "spot_oracle_gap_ppm": None,
        "perp_oracle_gap_ppm": None,
        "spot_perp_basis_ppm": (
            None
            if perp_price is None
            else float(relative_gap_ppm(perp_price, spot_price))
        ),
        "oracle_update_hazard_ppm": None,
        "spot_flow_imbalance_ppm": None if spot_flow is None else float(spot_flow),
        "perp_flow_imbalance_ppm": None if perp_flow is None else float(perp_flow),
        "pool_bull_share_ppm": None,
        "pool_log_total_bnb": None,
        "pool_recent_flow_imbalance_ppm": None,
        "pool_log_bet_count": None,
        "pool_log_unique_bettors": None,
        "prior_bull_rate_20_ppm": None if prior_bull is None else float(prior_bull),
        "prior_abs_return_12_ppm": (
            None if prior_abs_return is None else float(prior_abs_return)
        ),
    }
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=record.epoch,
        decision_timestamp_ms=decision_ms,
        values=values,
    )


def build_legacy_clickhouse_feature_rows(
    rounds: tuple[LegacyRoundRecord, ...],
    source: ClickHouseParameterizedJsonSource,
    *,
    spot_timestamp_unit: TimestampUnit,
    spot_availability_lag_ms: int,
    perp_timestamp_unit: TimestampUnit = "milliseconds",
    perp_availability_lag_ms: int = 0,
    include_perp: bool = True,
    chunk_span_ms: int = 3_600_000,
    feature_lead_seconds: int = 20,
    flow_lookback_ms: int = 60_000,
    max_spot_age_ms: int = 5_000,
    max_perp_age_ms: int = 5_000,
) -> LegacyFeatureBuildResult:
    if spot_availability_lag_ms < 0 or perp_availability_lag_ms < 0:
        raise ValueError("Binance availability lag must be non-negative")
    if chunk_span_ms <= 0 or flow_lookback_ms <= 0:
        raise ValueError("chunk span and flow lookback must be positive")
    if feature_lead_seconds <= 0:
        raise ValueError("feature_lead_seconds must be positive")
    if max_spot_age_ms <= 0 or max_perp_age_ms <= 0:
        raise ValueError("max price ages must be positive")

    ordered = tuple(sorted(rounds, key=lambda record: record.epoch))
    candidates = tuple(
        record
        for record in ordered
        if record.oracle_called and record.label in {"bull", "bear", "tie"}
    )
    groups = _group_rounds(
        candidates,
        feature_lead_seconds=feature_lead_seconds,
        chunk_span_ms=chunk_span_ms,
    )
    rows: list[ResearchFeatureRow] = []
    grouped_count = sum(len(group) for _, group in groups)
    skipped_invalid = len(candidates) - grouped_count
    skipped_no_spot = 0
    max_spot_rows = 0
    max_perp_rows = 0
    spot_query_start: int | None = None
    perp_query_start: int | None = None
    query_end: int | None = None

    for chunk_start, chunk_rounds in groups:
        chunk_end = chunk_start + chunk_span_ms
        spot_start = max(
            0,
            chunk_start - max(flow_lookback_ms, max_spot_age_ms),
        )
        perp_start = max(
            0,
            chunk_start - max(flow_lookback_ms, max_perp_age_ms),
        )
        spot_query_start = spot_start if spot_query_start is None else min(
            spot_query_start,
            spot_start,
        )
        if include_perp:
            perp_query_start = perp_start if perp_query_start is None else min(
                perp_query_start,
                perp_start,
            )
        query_end = chunk_end if query_end is None else max(query_end, chunk_end)

        spot_trades = load_binance_trade_window(
            source,
            market="BNBUSD",
            venue="spot",
            timestamp_unit=spot_timestamp_unit,
            availability_lag_ms=spot_availability_lag_ms,
            start_timestamp_ms=spot_start,
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
                start_timestamp_ms=perp_start,
                end_timestamp_ms=chunk_end,
            )
        max_spot_rows = max(max_spot_rows, len(spot_trades))
        max_perp_rows = max(max_perp_rows, len(perp_trades))
        spot_index = TradeTimeIndex.build(spot_trades, expected_symbol="BNBUSDT")
        perp_index = TradeTimeIndex.build(perp_trades, expected_symbol="BNBUSDT")
        for record in chunk_rounds:
            row = _feature_row(
                ordered,
                record,
                spot_index,
                perp_index,
                feature_lead_seconds=feature_lead_seconds,
                flow_lookback_ms=flow_lookback_ms,
                max_spot_age_ms=max_spot_age_ms,
                max_perp_age_ms=max_perp_age_ms,
                include_perp=include_perp,
            )
            if row is None:
                skipped_no_spot += 1
                continue
            rows.append(row)

    return LegacyFeatureBuildResult(
        rows=tuple(rows),
        candidate_rounds=len(candidates),
        skipped_invalid_round=skipped_invalid,
        skipped_no_spot=skipped_no_spot,
        chunks_loaded=len(groups),
        max_spot_chunk_rows=max_spot_rows,
        max_perp_chunk_rows=max_perp_rows,
        spot_query_start_ms=spot_query_start,
        perp_query_start_ms=perp_query_start,
        query_end_ms=query_end,
    )
