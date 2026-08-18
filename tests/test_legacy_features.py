from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace

from pancake_prediction.binance import AggTrade
from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.legacy_features import build_legacy_clickhouse_feature_rows
from pancake_prediction.legacy_rounds import LegacyRoundRecord


class TradeSource:
    def __init__(
        self,
        *,
        spot: tuple[AggTrade, ...],
        perp: tuple[AggTrade, ...] = (),
    ) -> None:
        self.trades = {"spot": spot, "um_futures": perp}
        self.calls: list[dict[str, QueryParameter]] = []

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        if "FROM binance_agg_trades FINAL" not in query:
            raise AssertionError("legacy feature builder must read deduplicated FINAL rows")
        if parameters is None:
            raise AssertionError("legacy feature builder must use typed parameters")
        values = dict(parameters)
        self.calls.append(values)
        venue = str(values["venue"])
        start = int(values["start_timestamp_ms"])
        end = int(values["end_timestamp_ms"])
        for trade in self.trades[venue]:
            if start <= trade.trade_timestamp_ms < end:
                yield {
                    "symbol": trade.symbol,
                    "event_timestamp_ms": trade.event_timestamp_ms,
                    "trade_timestamp_ms": trade.trade_timestamp_ms,
                    "price_e8": trade.price_e8,
                    "quantity_e8": trade.quantity_e8,
                    "aggressive_side": trade.aggressive_side,
                    "aggregate_trade_id": trade.aggregate_trade_id,
                }


def _round(
    epoch: int,
    *,
    start: int,
    lock: int,
    close: int,
    label: str = "bull",
    lock_price: int = 30_000_000_000,
    close_price: int | None = None,
) -> LegacyRoundRecord:
    resolved_close = (
        lock_price + 100_000_000
        if close_price is None and label == "bull"
        else lock_price - 100_000_000
        if close_price is None and label == "bear"
        else lock_price
        if close_price is None
        else close_price
    )
    return LegacyRoundRecord(
        epoch=epoch,
        start_timestamp=start,
        lock_timestamp=lock,
        close_timestamp=close,
        lock_price_e8=lock_price,
        close_price_e8=resolved_close,
        lock_oracle_id=10_000 + epoch,
        close_oracle_id=20_000 + epoch,
        total_amount_wei=2 * 10**18,
        bull_amount_wei=12 * 10**17,
        bear_amount_wei=8 * 10**17,
        reward_base_cal_amount_wei=12 * 10**17,
        reward_amount_wei=194 * 10**16,
        oracle_called=True,
    )


def _trade(
    trade_id: int,
    timestamp_ms: int,
    *,
    event_timestamp_ms: int | None = None,
    price_e8: int = 30_000_000_000,
    side: str = "buy",
) -> AggTrade:
    return AggTrade(
        symbol="BNBUSDT",
        event_timestamp_ms=(
            timestamp_ms if event_timestamp_ms is None else event_timestamp_ms
        ),
        trade_timestamp_ms=timestamp_ms,
        price_e8=price_e8,
        quantity_e8=100_000_000,
        aggressive_side=side,
        aggregate_trade_id=trade_id,
    )


def test_legacy_feature_cutoff_excludes_trade_not_available_at_decision() -> None:
    target = _round(3, start=1_000, lock=1_300, close=1_600)
    decision_ms = 1_280_000
    source = TradeSource(
        spot=(
            _trade(1, 1_279_000, event_timestamp_ms=1_279_100, price_e8=30_010_000_000),
            _trade(2, 1_279_500, event_timestamp_ms=decision_ms, price_e8=39_999_000_000),
        )
    )

    result = build_legacy_clickhouse_feature_rows(
        (target,),
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        include_perp=False,
        chunk_span_ms=1_000_000,
        feature_lead_seconds=20,
        flow_lookback_ms=60_000,
        max_spot_age_ms=5_000,
    )

    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.decision_timestamp_ms == decision_ms
    assert row.values["spot_flow_imbalance_ppm"] == 1_000_000.0
    assert row.values["pool_bull_share_ppm"] is None
    assert row.values["spot_oracle_gap_ppm"] is None


def test_target_lock_close_price_mutation_cannot_change_legacy_feature_row() -> None:
    prior = _round(1, start=400, lock=700, close=900, label="bear")
    target = _round(3, start=1_000, lock=1_300, close=1_600, label="bull")
    spot = (
        _trade(1, 1_279_000, price_e8=30_010_000_000),
    )
    perp = (
        _trade(11, 1_279_000, price_e8=30_020_000_000, side="sell"),
    )
    original = build_legacy_clickhouse_feature_rows(
        (prior, target),
        TradeSource(spot=spot, perp=perp),
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        perp_timestamp_unit="milliseconds",
        perp_availability_lag_ms=0,
        feature_lead_seconds=20,
        max_spot_age_ms=5_000,
        max_perp_age_ms=5_000,
    )
    mutated_target = replace(
        target,
        lock_price_e8=99_999_999_999,
        close_price_e8=1,
        bull_amount_wei=9 * 10**20,
        bear_amount_wei=8 * 10**20,
        total_amount_wei=17 * 10**20,
    )
    mutated = build_legacy_clickhouse_feature_rows(
        (prior, mutated_target),
        TradeSource(spot=spot, perp=perp),
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        perp_timestamp_unit="milliseconds",
        perp_availability_lag_ms=0,
        feature_lead_seconds=20,
        max_spot_age_ms=5_000,
        max_perp_age_ms=5_000,
    )

    original_target = next(row for row in original.rows if row.epoch == 3)
    mutated_target_row = next(row for row in mutated.rows if row.epoch == 3)
    assert original_target == mutated_target_row


def test_legacy_history_features_only_use_rounds_closed_before_decision() -> None:
    prior_known = _round(1, start=300, lock=600, close=800, label="bull")
    prior_future_close = _round(2, start=900, lock=1_200, close=1_290, label="bear")
    target = _round(3, start=1_000, lock=1_300, close=1_600, label="bull")
    source = TradeSource(
        spot=(
            _trade(1, 1_279_000),
        )
    )

    result = build_legacy_clickhouse_feature_rows(
        (prior_known, prior_future_close, target),
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        include_perp=False,
        feature_lead_seconds=20,
        max_spot_age_ms=5_000,
    )
    target_row = next(row for row in result.rows if row.epoch == 3)

    assert target_row.values["prior_bull_rate_20_ppm"] == 1_000_000.0
    expected_return = (
        abs(prior_known.close_price_e8 - prior_known.lock_price_e8)
        * 1_000_000
        // prior_known.lock_price_e8
    )
    assert target_row.values["prior_abs_return_12_ppm"] == float(expected_return)


def test_legacy_chunk_builder_reuses_queries_and_skips_missing_spot() -> None:
    rounds = (
        _round(3, start=1_000, lock=1_300, close=1_600),
        _round(4, start=1_100, lock=1_400, close=1_700),
    )
    source = TradeSource(
        spot=(
            _trade(1, 1_279_000),
        ),
        perp=(
            _trade(11, 1_279_000, side="sell"),
        ),
    )

    result = build_legacy_clickhouse_feature_rows(
        rounds,
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        perp_timestamp_unit="milliseconds",
        perp_availability_lag_ms=0,
        chunk_span_ms=1_000_000,
        feature_lead_seconds=20,
        max_spot_age_ms=5_000,
        max_perp_age_ms=5_000,
    )

    assert result.chunks_loaded == 1
    assert len(source.calls) == 2
    assert {str(call["venue"]) for call in source.calls} == {"spot", "um_futures"}
    assert len(result.rows) == 1
    assert result.rows[0].epoch == 3
    assert result.skipped_no_spot == 1
    assert result.spot_query_start_ms == 940_000
    assert result.perp_query_start_ms == 940_000
    assert result.query_end_ms == 2_000_000
