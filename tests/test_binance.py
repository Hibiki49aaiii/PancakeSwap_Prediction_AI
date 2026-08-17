from pancake_prediction.binance import aggregate_order_flow, parse_agg_trade


def test_parse_agg_trade_maps_maker_flag_to_aggressor() -> None:
    buy = parse_agg_trade(
        {
            "e": "aggTrade",
            "E": 1001,
            "T": 1000,
            "s": "BTCUSDT",
            "a": 7,
            "p": "60000.25",
            "q": "0.10",
            "m": False,
        }
    )
    sell = parse_agg_trade(
        {
            "e": "aggTrade",
            "E": 1003,
            "T": 1002,
            "s": "BTCUSDT",
            "a": 8,
            "p": "60000.00",
            "q": "0.20",
            "m": True,
        }
    )
    assert buy.aggressive_side == "buy"
    assert sell.aggressive_side == "sell"
    window = aggregate_order_flow((buy, sell), start_timestamp_ms=900, end_timestamp_ms=1100)
    assert window.trade_count == 2
    assert window.buy_notional_e16 > 0
    assert window.sell_notional_e16 > window.buy_notional_e16
    assert window.imbalance_ppm is not None
    assert window.imbalance_ppm < 0
