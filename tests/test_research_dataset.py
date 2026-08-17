from __future__ import annotations

import pytest

from pancake_prediction.binance import AggTrade
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord
from pancake_prediction.research_dataset import TradeTimeIndex, build_research_dataset


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
        block_number=200 + index,
        block_hash="0x" + f"{200 + index:064x}",
        block_timestamp=timestamp,
        tx_hash="0x" + f"{200 + index:064x}",
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
    *,
    trade_timestamp_ms: int,
    event_timestamp_ms: int,
    price_e8: int,
    side: str,
    symbol: str = "BNBUSDT",
) -> AggTrade:
    return AggTrade(
        symbol=symbol,
        event_timestamp_ms=event_timestamp_ms,
        trade_timestamp_ms=trade_timestamp_ms,
        price_e8=price_e8,
        quantity_e8=100_000_000,
        aggressive_side=side,
        aggregate_trade_id=trade_id,
    )


def test_trade_time_index_rejects_cross_market_contamination() -> None:
    with pytest.raises(ValueError, match="unexpected Binance symbol"):
        TradeTimeIndex.build(
            (
                _trade(
                    1,
                    trade_timestamp_ms=1_000,
                    event_timestamp_ms=1_001,
                    price_e8=60_000_000_000,
                    side="buy",
                    symbol="BTCUSDT",
                ),
            ),
            expected_symbol="BNBUSDT",
        )


def test_trade_time_index_rejects_duplicate_aggregate_ids() -> None:
    trades = (
        _trade(
            1,
            trade_timestamp_ms=1_000,
            event_timestamp_ms=1_001,
            price_e8=60_000_000_000,
            side="buy",
        ),
        _trade(
            1,
            trade_timestamp_ms=1_002,
            event_timestamp_ms=1_003,
            price_e8=60_000_000_001,
            side="sell",
        ),
    )
    with pytest.raises(ValueError, match="duplicate aggregate trade id"):
        TradeTimeIndex.build(trades, expected_symbol="BNBUSDT")


def test_research_dataset_uses_only_market_data_available_before_cutoff() -> None:
    replay = ReplaySnapshot(
        1,
        "BNBUSD",
        "a" * 64,
        (
            _round(1, 100, 500, "bull"),
            _round(2, 500, 900, "bear"),
            _round(3, 1000, 1600, "bull"),
        ),
    )
    events = (
        _bet(3, 1200, 100, "BetBull", 1),
        _bet(3, 1250, 50, "BetBear", 2),
        *tuple(_chainlink(index, 1260 + index) for index in range(10)),
    )
    spot = (
        _trade(
            1,
            trade_timestamp_ms=1_279_000,
            event_timestamp_ms=1_279_010,
            price_e8=60_100_000_000,
            side="buy",
        ),
        _trade(
            2,
            trade_timestamp_ms=1_279_900,
            event_timestamp_ms=1_280_010,
            price_e8=59_000_000_000,
            side="sell",
        ),
    )

    result = build_research_dataset(
        replay,
        events,
        spot,
        flow_lookback_ms=20_000,
        max_spot_age_ms=5_000,
        oracle_history_updates=10,
        oracle_hazard_min_intervals=2,
    )

    assert result.candidate_rounds == 3
    assert result.pool_feature_rows == 3
    assert result.row_count == 1
    row = result.research_feature_rows[0]
    assert row.epoch == 3
    assert row.decision_timestamp_ms == 1_280_000
    assert row.values["spot_flow_imbalance_ppm"] == 1_000_000.0
    assert row.values["spot_oracle_gap_ppm"] is not None
