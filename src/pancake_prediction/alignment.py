from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .alpha import AlphaFeatureRow, TimedPrice, build_alpha_feature_row
from .binance import AggTrade, OrderFlowWindow, aggregate_order_flow
from .replay import ChainEvent


@dataclass(frozen=True, slots=True)
class AvailablePrice:
    source: str
    source_timestamp_ms: int
    available_at_ms: int
    price_e8: int
    source_order: tuple[int, ...] = ()

    def validate(self) -> None:
        if self.source_timestamp_ms < 0 or self.available_at_ms < 0:
            raise ValueError("price timestamps must be non-negative")
        if self.price_e8 <= 0:
            raise ValueError("price must be positive")
        if self.available_at_ms < self.source_timestamp_ms:
            raise ValueError("price cannot become available before its source timestamp")
        if any(value < 0 for value in self.source_order):
            raise ValueError("source ordering values must be non-negative")


@dataclass(frozen=True, slots=True)
class AlignedAlphaInputs:
    decision_timestamp_ms: int
    chainlink: TimedPrice
    spot: TimedPrice
    perp: TimedPrice | None
    spot_flow: OrderFlowWindow
    perp_flow: OrderFlowWindow | None
    oracle_update_timestamps_ms: tuple[int, ...]


def latest_available_price(
    observations: Sequence[AvailablePrice],
    *,
    decision_timestamp_ms: int,
    max_age_ms: int | None = None,
) -> AvailablePrice | None:
    if decision_timestamp_ms < 0:
        raise ValueError("decision timestamp must be non-negative")
    if max_age_ms is not None and max_age_ms < 0:
        raise ValueError("max_age_ms must be non-negative")

    eligible: list[AvailablePrice] = []
    for observation in observations:
        observation.validate()
        if observation.available_at_ms >= decision_timestamp_ms:
            continue
        if observation.source_timestamp_ms >= decision_timestamp_ms:
            continue
        if max_age_ms is not None:
            age = decision_timestamp_ms - observation.source_timestamp_ms
            if age > max_age_ms:
                continue
        eligible.append(observation)
    if not eligible:
        return None

    eligible.sort(
        key=lambda item: (
            item.source_timestamp_ms,
            item.available_at_ms,
            item.source_order,
            item.price_e8,
            item.source,
        )
    )
    selected = eligible[-1]
    same_identity = [
        item
        for item in eligible
        if item.source == selected.source
        and item.source_timestamp_ms == selected.source_timestamp_ms
        and item.available_at_ms == selected.available_at_ms
        and item.source_order == selected.source_order
    ]
    if len({item.price_e8 for item in same_identity}) > 1:
        raise ValueError("conflicting prices share the same source event identity")
    return selected


def chainlink_available_prices(
    events: Iterable[ChainEvent],
    *,
    availability_lag_ms: int = 0,
) -> tuple[AvailablePrice, ...]:
    if availability_lag_ms < 0:
        raise ValueError("Chainlink availability lag must be non-negative")
    observations: list[AvailablePrice] = []
    for event in events:
        if event.event_name != "AnswerUpdated":
            continue
        current = event.decoded.get("current")
        updated_at = event.decoded.get("updatedAt")
        if not isinstance(current, int) or not isinstance(updated_at, int):
            continue
        if current <= 0 or updated_at < 0:
            continue
        source_timestamp_ms = updated_at * 1_000
        block_available_ms = event.block_timestamp * 1_000 + availability_lag_ms
        available_at_ms = max(source_timestamp_ms, block_available_ms)
        observations.append(
            AvailablePrice(
                source="chainlink",
                source_timestamp_ms=source_timestamp_ms,
                available_at_ms=available_at_ms,
                price_e8=current,
                source_order=(event.block_number, event.tx_index, event.log_index),
            )
        )
    observations.sort(
        key=lambda item: (
            item.source_timestamp_ms,
            item.available_at_ms,
            item.source_order,
            item.price_e8,
        )
    )
    return tuple(observations)


def binance_available_prices(
    trades: Iterable[AggTrade], *, source: str
) -> tuple[AvailablePrice, ...]:
    observations = [
        AvailablePrice(
            source=source,
            source_timestamp_ms=trade.trade_timestamp_ms,
            available_at_ms=max(trade.trade_timestamp_ms, trade.event_timestamp_ms),
            price_e8=trade.price_e8,
            source_order=(trade.aggregate_trade_id,),
        )
        for trade in trades
    ]
    observations.sort(
        key=lambda item: (
            item.source_timestamp_ms,
            item.available_at_ms,
            item.source_order,
            item.price_e8,
        )
    )
    return tuple(observations)


def _timed_price(observation: AvailablePrice) -> TimedPrice:
    return TimedPrice(
        source=observation.source,
        observed_at_ms=observation.source_timestamp_ms,
        price_e8=observation.price_e8,
    )


def build_aligned_alpha_inputs(
    *,
    decision_timestamp_ms: int,
    chainlink_events: Iterable[ChainEvent],
    spot_trades: Sequence[AggTrade],
    perp_trades: Sequence[AggTrade] = (),
    flow_lookback_ms: int = 60_000,
    max_spot_age_ms: int = 5_000,
    max_perp_age_ms: int = 5_000,
    max_chainlink_age_ms: int | None = None,
    chainlink_availability_lag_ms: int = 0,
) -> AlignedAlphaInputs | None:
    if flow_lookback_ms <= 0:
        raise ValueError("flow_lookback_ms must be positive")
    if decision_timestamp_ms < flow_lookback_ms:
        raise ValueError("decision timestamp precedes flow lookback window")

    chainlink_prices = chainlink_available_prices(
        chainlink_events,
        availability_lag_ms=chainlink_availability_lag_ms,
    )
    spot_prices = binance_available_prices(spot_trades, source="binance-spot")
    perp_prices = binance_available_prices(perp_trades, source="binance-perp")

    chainlink = latest_available_price(
        chainlink_prices,
        decision_timestamp_ms=decision_timestamp_ms,
        max_age_ms=max_chainlink_age_ms,
    )
    spot = latest_available_price(
        spot_prices,
        decision_timestamp_ms=decision_timestamp_ms,
        max_age_ms=max_spot_age_ms,
    )
    perp = latest_available_price(
        perp_prices,
        decision_timestamp_ms=decision_timestamp_ms,
        max_age_ms=max_perp_age_ms,
    )
    if chainlink is None or spot is None:
        return None

    start_timestamp_ms = decision_timestamp_ms - flow_lookback_ms
    spot_flow = aggregate_order_flow(
        spot_trades,
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=decision_timestamp_ms,
    )
    perp_flow = None
    if perp_trades:
        perp_flow = aggregate_order_flow(
            perp_trades,
            start_timestamp_ms=start_timestamp_ms,
            end_timestamp_ms=decision_timestamp_ms,
        )

    oracle_updates = tuple(
        observation.source_timestamp_ms
        for observation in chainlink_prices
        if observation.available_at_ms < decision_timestamp_ms
        and observation.source_timestamp_ms < decision_timestamp_ms
    )
    return AlignedAlphaInputs(
        decision_timestamp_ms=decision_timestamp_ms,
        chainlink=_timed_price(chainlink),
        spot=_timed_price(spot),
        perp=None if perp is None else _timed_price(perp),
        spot_flow=spot_flow,
        perp_flow=perp_flow,
        oracle_update_timestamps_ms=oracle_updates,
    )


def build_aligned_alpha_feature_row(
    *,
    market: str,
    epoch: int,
    aligned: AlignedAlphaInputs,
    oracle_hazard_horizon_ms: int = 5_000,
    oracle_hazard_min_intervals: int = 8,
) -> AlphaFeatureRow:
    return build_alpha_feature_row(
        market=market,
        epoch=epoch,
        decision_timestamp_ms=aligned.decision_timestamp_ms,
        chainlink=aligned.chainlink,
        spot=aligned.spot,
        perp=aligned.perp,
        spot_flow_imbalance_ppm=aligned.spot_flow.imbalance_ppm,
        perp_flow_imbalance_ppm=(
            None if aligned.perp_flow is None else aligned.perp_flow.imbalance_ppm
        ),
        oracle_update_timestamps_ms=aligned.oracle_update_timestamps_ms,
        oracle_hazard_horizon_ms=oracle_hazard_horizon_ms,
        oracle_hazard_min_intervals=oracle_hazard_min_intervals,
    )
