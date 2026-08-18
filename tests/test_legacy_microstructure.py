from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from pancake_prediction.baseline import ResearchFeatureRow
from pancake_prediction.binance import AggTrade
from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.legacy_microstructure import enrich_legacy_microstructure_rows


class TradeSource:
    def __init__(self, spot: tuple[AggTrade, ...], perp: tuple[AggTrade, ...]) -> None:
        self.trades = {"spot": spot, "um_futures": perp}

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        assert "FROM binance_agg_trades FINAL" in query
        if parameters is None:
            raise AssertionError("microstructure queries must be parameterized")
        venue = str(parameters["venue"])
        start = int(parameters["start_timestamp_ms"])
        end = int(parameters["end_timestamp_ms"])
        for trade in self.trades[venue]:
            if not start <= trade.trade_timestamp_ms < end:
                continue
            yield {
                "symbol": trade.symbol,
                "event_timestamp_ms": trade.event_timestamp_ms,
                "trade_timestamp_ms": trade.trade_timestamp_ms,
                "price_e8": trade.price_e8,
                "quantity_e8": trade.quantity_e8,
                "aggressive_side": trade.aggressive_side,
                "aggregate_trade_id": trade.aggregate_trade_id,
            }


def _trade(
    trade_id: int,
    trade_timestamp_ms: int,
    event_timestamp_ms: int,
    price_e8: int,
    side: str,
) -> AggTrade:
    return AggTrade(
        symbol="BNBUSDT",
        event_timestamp_ms=event_timestamp_ms,
        trade_timestamp_ms=trade_timestamp_ms,
        price_e8=price_e8,
        quantity_e8=100_000_000,
        aggressive_side=side,
        aggregate_trade_id=trade_id,
    )


def _row() -> ResearchFeatureRow:
    return ResearchFeatureRow(
        market="BNBUSD",
        epoch=100,
        decision_timestamp_ms=100_000,
        values={"spot_perp_basis_ppm": 0.0},
    )


def test_microstructure_uses_only_trades_available_before_cutoff() -> None:
    source = TradeSource(
        spot=(
            _trade(1, 95_500, 95_750, 10_000_000_000, "sell"),
            _trade(2, 98_000, 98_250, 10_100_000_000, "buy"),
            _trade(3, 99_900, 100_000, 99_900_000_000, "buy"),
        ),
        perp=(
            _trade(11, 96_000, 96_250, 20_000_000_000, "sell"),
            _trade(12, 99_000, 99_250, 20_200_000_000, "buy"),
        ),
    )

    result = enrich_legacy_microstructure_rows(
        (_row(),),
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=250,
        perp_timestamp_unit="milliseconds",
        perp_availability_lag_ms=250,
        chunk_span_ms=60_000,
    )

    assert len(result.rows) == 1
    values = result.rows[0].values
    assert values["spot_return_5s_ppm"] == 10_000.0
    assert values["spot_trade_count_5s"] == 2.0
    assert values["spot_return_20s_ppm"] == 10_000.0
    assert values["perp_return_5s_ppm"] == 10_000.0
    assert values["perp_trade_count_60s"] == 2.0
    assert result.max_spot_chunk_rows == 3
    assert result.max_perp_chunk_rows == 2


def test_microstructure_without_perp_marks_perp_features_unavailable() -> None:
    source = TradeSource(
        spot=(_trade(1, 98_000, 98_100, 10_000_000_000, "buy"),),
        perp=(),
    )

    result = enrich_legacy_microstructure_rows(
        (_row(),),
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=100,
        include_perp=False,
        chunk_span_ms=60_000,
    )

    values = result.rows[0].values
    assert values["perp_return_5s_ppm"] is None
    assert values["perp_flow_imbalance_20s_ppm"] is None
    assert values["perp_trade_count_60s"] is None


def test_microstructure_rejects_invalid_query_configuration() -> None:
    source = TradeSource((), ())
    with pytest.raises(ValueError, match="non-negative"):
        enrich_legacy_microstructure_rows(
            (_row(),),
            source,
            spot_timestamp_unit="milliseconds",
            spot_availability_lag_ms=-1,
        )
    with pytest.raises(ValueError, match="chunk_span_ms"):
        enrich_legacy_microstructure_rows(
            (_row(),),
            source,
            spot_timestamp_unit="milliseconds",
            spot_availability_lag_ms=0,
            chunk_span_ms=0,
        )
