from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace

from pancake_prediction.binance import AggTrade
from pancake_prediction.clickhouse import QueryParameter
from pancake_prediction.recent_features import build_recent_canonical_cex_features
from pancake_prediction.replay import ChainEvent, ReplaySnapshot, RoundRecord


class TradeSource:
    def __init__(
        self,
        *,
        spot: tuple[AggTrade, ...],
        perp: tuple[AggTrade, ...] = (),
    ) -> None:
        self.trades = {"spot": spot, "um_futures": perp}

    def query_json_rows(
        self,
        query: str,
        *,
        parameters: Mapping[str, QueryParameter] | None = None,
    ) -> Iterator[dict[str, object]]:
        if "FROM binance_agg_trades FINAL" not in query:
            raise AssertionError("recent features must use FINAL ClickHouse reads")
        if parameters is None:
            raise AssertionError("recent features must use typed query parameters")
        venue = str(parameters["venue"])
        start = int(parameters["start_timestamp_ms"])
        end = int(parameters["end_timestamp_ms"])
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


def _round(epoch: int, *, label: str = "bull") -> RoundRecord:
    start = 1_000 + (epoch - 1) * 300
    lock = start + 300
    end = lock + 300
    lock_price = 30_000_000_000
    close_price = lock_price + (100_000_000 if label == "bull" else -100_000_000)
    return RoundRecord(
        epoch=epoch,
        start_block=100 + epoch,
        start_timestamp=start,
        lock_block=110 + epoch,
        lock_timestamp=lock,
        lock_round_id=1_000 + epoch,
        lock_price=lock_price,
        end_block=120 + epoch,
        end_timestamp=end,
        close_round_id=2_000 + epoch,
        close_price=close_price,
        bull_amount_wei=12 * 10**17,
        bear_amount_wei=8 * 10**17,
        total_amount_wei=2 * 10**18,
        bet_count=10,
        reward_base_cal_amount_wei=12 * 10**17,
        reward_amount_wei=194 * 10**16,
        treasury_amount_wei=6 * 10**16,
        label=label,
        issues=(),
    )


def _start_event(record: RoundRecord) -> ChainEvent:
    assert record.start_block is not None
    assert record.start_timestamp is not None
    return ChainEvent(
        block_number=record.start_block,
        block_hash=f"0x{record.start_block:064x}",
        block_timestamp=record.start_timestamp,
        tx_hash=f"0x{record.epoch:064x}",
        tx_index=0,
        log_index=0,
        event_name="StartRound",
        decoded={"epoch": record.epoch},
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


def _fixture() -> tuple[ReplaySnapshot, tuple[ChainEvent, ...]]:
    rounds = (_round(1, label="bull"), _round(2, label="bear"), _round(3, label="bull"))
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="a" * 64,
        rounds=rounds,
    )
    return replay, tuple(_start_event(record) for record in rounds)


def test_recent_feature_cutoff_uses_known_round_timing_not_target_outcome() -> None:
    replay, events = _fixture()
    target = replay.rounds[2]
    assert target.start_timestamp is not None
    decision_ms = (target.start_timestamp + 300 - 20) * 1_000
    source = TradeSource(
        spot=(
            _trade(1, decision_ms - 1_000, event_timestamp_ms=decision_ms - 900),
            _trade(
                2,
                decision_ms - 500,
                event_timestamp_ms=decision_ms,
                price_e8=99_999_000_000,
            ),
        )
    )

    result = build_recent_canonical_cex_features(
        replay,
        events,
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        include_perp=False,
        feature_lead_seconds=20,
        max_spot_age_ms=5_000,
    )
    row = next(item for item in result.rows if item.epoch == 3)

    assert row.decision_timestamp_ms == decision_ms
    assert row.values["spot_flow_imbalance_ppm"] == 1_000_000.0
    assert row.values["pool_bull_share_ppm"] is None
    assert row.values["oracle_age_ms"] is None


def test_target_final_price_and_pool_mutation_cannot_change_recent_feature_row() -> None:
    replay, events = _fixture()
    target = replay.rounds[2]
    assert target.start_timestamp is not None
    decision_ms = (target.start_timestamp + 300 - 20) * 1_000
    spot = (_trade(1, decision_ms - 1_000, price_e8=30_010_000_000),)
    perp = (_trade(2, decision_ms - 1_000, price_e8=30_020_000_000, side="sell"),)

    original = build_recent_canonical_cex_features(
        replay,
        events,
        TradeSource(spot=spot, perp=perp),
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        feature_lead_seconds=20,
    )
    mutated_target = replace(
        target,
        lock_price=99_999_999_999,
        close_price=1,
        bull_amount_wei=9 * 10**20,
        bear_amount_wei=8 * 10**20,
        total_amount_wei=17 * 10**20,
    )
    mutated_replay = replace(
        replay,
        rounds=(replay.rounds[0], replay.rounds[1], mutated_target),
    )
    mutated = build_recent_canonical_cex_features(
        mutated_replay,
        events,
        TradeSource(spot=spot, perp=perp),
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        feature_lead_seconds=20,
    )

    original_row = next(item for item in original.rows if item.epoch == 3)
    mutated_row = next(item for item in mutated.rows if item.epoch == 3)
    assert original_row == mutated_row


def test_recent_history_feature_excludes_prior_round_closed_after_decision() -> None:
    replay, events = _fixture()
    target = replay.rounds[2]
    assert target.start_timestamp is not None
    decision_ms = (target.start_timestamp + 300 - 20) * 1_000
    late_prior = replace(
        replay.rounds[1],
        end_timestamp=decision_ms // 1_000 + 1,
    )
    changed = replace(
        replay,
        rounds=(replay.rounds[0], late_prior, target),
    )
    source = TradeSource(spot=(_trade(1, decision_ms - 1_000),))

    result = build_recent_canonical_cex_features(
        changed,
        events,
        source,
        spot_timestamp_unit="milliseconds",
        spot_availability_lag_ms=0,
        include_perp=False,
        feature_lead_seconds=20,
    )
    row = next(item for item in result.rows if item.epoch == 3)

    assert row.values["prior_bull_rate_20_ppm"] == 1_000_000.0
