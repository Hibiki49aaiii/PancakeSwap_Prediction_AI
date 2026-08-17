from pancake_prediction.backtest import BacktestConfig
from pancake_prediction.features import build_pool_feature_row
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


def _round(epoch: int, start: int, end: int, label: str, lock_price: int, close_price: int) -> RoundRecord:
    return RoundRecord(
        epoch=epoch,
        start_block=epoch * 10,
        start_timestamp=start,
        lock_block=epoch * 10 + 1,
        lock_timestamp=start + 300,
        lock_round_id=epoch,
        lock_price=lock_price,
        end_block=epoch * 10 + 2,
        end_timestamp=end,
        close_round_id=epoch + 1,
        close_price=close_price,
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


def test_pool_features_use_only_information_known_before_feature_cutoff() -> None:
    settled = _round(1, 100, 500, "bull", 100, 102)
    future_settlement = _round(2, 400, 1500, "bear", 100, 95)
    target = _round(3, 1000, 1600, "bull", 100, 101)
    replay = ReplaySnapshot(1, "BNBUSD", "a" * 64, (settled, future_settlement, target))
    events = (
        _bet(3, 1200, 100, "BetBull", 1),
        _bet(3, 1250, 50, "BetBear", 2),
        _bet(3, 1280, 999, "BetBear", 3),
    )
    row = build_pool_feature_row(
        replay,
        target,
        events,
        BacktestConfig(),
        feature_lead_seconds=20,
    )
    assert row is not None
    assert row.feature_timestamp == 1280
    assert row.bull_pool_wei == 100
    assert row.bear_pool_wei == 50
    assert row.bet_count == 2
    assert row.unique_bettors == 2
    assert row.last_60s_bear_wei == 50
    assert row.prior_bull_rate_20_ppm == 1_000_000
    assert row.prior_abs_return_12_ppm == 20_000
