from __future__ import annotations

from collections.abc import Iterator, Mapping

from pancake_prediction.binance import AggTrade
from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.clickhouse_dataset import build_chunked_clickhouse_research_dataset
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord
from pancake_prediction.research_dataset import build_research_dataset


class TradeSource:
    def __init__(
        self,
        *,
        spot: tuple[AggTrade, ...],
        perp: tuple[AggTrade, ...],
    ) -> None:
        self.trades = {"spot": spot, "um_futures": perp}
        self.calls: list[dict[str, QueryParameter]] = []

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        assert "FROM binance_agg_trades FINAL" in query
        assert parameters is not None
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


def _round(epoch: int, start: int, end: int, label: str) -> RoundRecord:
    return RoundRecord(
        epoch=epoch,
        start_block=epoch * 10,
        start_timestamp=start,
        lock_block=epoch * 10 + 1,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=60_000_000_000,
        end_block=epoch * 10 + 2,
        end_timestamp=end,
        close_round_id=epoch + 1,
        close_price=60_100_000_000 if label == "bull" else 59_900_000_000,
        bull_amount_wei=0,
        bear_amount_wei=0,
        total_amount_wei=0,
        bet_count=0,
        reward_base_cal_amount_wei=None,
        reward_amount_wei=None,
        treasury_amount_wei=None,
        label=label,
        issues=(),
    )


def _bet(epoch: int, timestamp: int, amount: int, side: str, index: int) -> ChainEvent:
    return ChainEvent(
        block_number=100 + index,
        block_hash="0x" + f"{100 + index:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{index + 1:064x}",
        tx_index=0,
        log_index=index,
        event_name=side,
        decoded={
            "epoch": epoch,
            "amount": amount,
            "sender": "0x" + f"{index + 1:040x}",
        },
    )


def _chainlink(index: int, timestamp: int) -> ChainEvent:
    return ChainEvent(
        block_number=1_000 + index,
        block_hash="0x" + f"{1_000 + index:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{1_000 + index:064x}",
        tx_index=0,
        log_index=index,
        event_name="AnswerUpdated",
        decoded={
            "current": 60_000_000_000 + index,
            "roundId": index + 1,
            "updatedAt": timestamp - 1,
        },
    )


def _trade(
    trade_id: int,
    timestamp_ms: int,
    *,
    price_e8: int,
    side: str,
    lag_ms: int,
) -> AggTrade:
    return AggTrade(
        symbol="BNBUSDT",
        event_timestamp_ms=timestamp_ms + lag_ms,
        trade_timestamp_ms=timestamp_ms,
        price_e8=price_e8,
        quantity_e8=100_000_000,
        aggressive_side=side,
        aggregate_trade_id=trade_id,
    )


def test_chunked_clickhouse_dataset_matches_in_memory_builder_without_n_plus_one() -> None:
    replay = ReplaySnapshot(
        1,
        "BNBUSD",
        "a" * 64,
        (
            _round(3, 1_000, 1_600, "bull"),
            _round(4, 1_300, 1_900, "bear"),
        ),
    )
    chainlink = tuple(
        _chainlink(index, 1_100 + index * 20)
        for index in range(25)
    )
    events = (
        _bet(3, 1_250, 100, "BetBull", 1),
        _bet(4, 1_550, 200, "BetBear", 2),
        *chainlink,
    )
    spot = (
        _trade(1, 1_270_000, price_e8=60_050_000_000, side="buy", lag_ms=25),
        _trade(2, 1_279_000, price_e8=60_100_000_000, side="buy", lag_ms=25),
        _trade(3, 1_570_000, price_e8=60_020_000_000, side="sell", lag_ms=25),
        _trade(4, 1_579_000, price_e8=59_980_000_000, side="sell", lag_ms=25),
    )
    perp = (
        _trade(11, 1_275_000, price_e8=60_110_000_000, side="sell", lag_ms=40),
        _trade(12, 1_575_000, price_e8=59_990_000_000, side="buy", lag_ms=40),
    )
    expected = build_research_dataset(
        replay,
        events,
        spot,
        perp_trades=perp,
        flow_lookback_ms=60_000,
        max_spot_age_ms=20_000,
        max_perp_age_ms=20_000,
        oracle_history_updates=20,
        oracle_hazard_min_intervals=2,
    )

    source = TradeSource(spot=spot, perp=perp)
    actual = build_chunked_clickhouse_research_dataset(
        replay,
        events,
        source,
        spot_availability_lag_ms=25,
        perp_availability_lag_ms=40,
        chunk_span_ms=1_000_000,
        flow_lookback_ms=60_000,
        max_spot_age_ms=20_000,
        max_perp_age_ms=20_000,
        oracle_history_updates=20,
        oracle_hazard_min_intervals=2,
    )

    assert actual.dataset == expected
    assert actual.chunks_loaded == 1
    assert len(source.calls) == 2
    assert {str(call["venue"]) for call in source.calls} == {"spot", "um_futures"}
    assert actual.max_spot_chunk_rows == 4
    assert actual.max_perp_chunk_rows == 2
    assert actual.spot_query_start_ms == 940_000
    assert actual.perp_query_start_ms == 940_000
    assert actual.query_end_ms == 2_000_000


def test_chunked_builder_can_skip_perp_query() -> None:
    replay = ReplaySnapshot(1, "BNBUSD", "b" * 64, (_round(3, 1_000, 1_600, "bull"),))
    events = tuple(_chainlink(index, 1_100 + index * 20) for index in range(10))
    spot = (
        _trade(1, 1_279_000, price_e8=60_100_000_000, side="buy", lag_ms=25),
    )
    source = TradeSource(spot=spot, perp=())
    result = build_chunked_clickhouse_research_dataset(
        replay,
        events,
        source,
        spot_availability_lag_ms=25,
        include_perp=False,
        chunk_span_ms=1_000_000,
        flow_lookback_ms=60_000,
        max_spot_age_ms=20_000,
        oracle_history_updates=10,
        oracle_hazard_min_intervals=2,
    )
    assert result.dataset.row_count == 1
    assert len(source.calls) == 1
    assert source.calls[0]["venue"] == "spot"
    assert result.max_perp_chunk_rows == 0
    assert result.spot_query_start_ms == 940_000
    assert result.perp_query_start_ms is None
    assert result.query_end_ms == 2_000_000
