from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

PPM = 1_000_000


@dataclass(frozen=True, slots=True)
class TimedPrice:
    source: str
    observed_at_ms: int
    price_e8: int


@dataclass(frozen=True, slots=True)
class AlphaFeatureRow:
    market: str
    epoch: int
    decision_timestamp_ms: int
    chainlink_price_e8: int
    chainlink_observed_at_ms: int
    oracle_age_ms: int
    spot_price_e8: int
    spot_observed_at_ms: int
    perp_price_e8: int | None
    perp_observed_at_ms: int | None
    spot_oracle_gap_ppm: int
    perp_oracle_gap_ppm: int | None
    spot_perp_basis_ppm: int | None
    spot_flow_imbalance_ppm: int | None
    perp_flow_imbalance_ppm: int | None
    oracle_update_hazard_ppm: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def relative_gap_ppm(numerator_price_e8: int, reference_price_e8: int) -> int:
    if reference_price_e8 <= 0 or numerator_price_e8 <= 0:
        raise ValueError("prices must be positive")
    return (numerator_price_e8 - reference_price_e8) * PPM // reference_price_e8


def empirical_oracle_update_hazard_ppm(
    update_timestamps_ms: Iterable[int],
    *,
    decision_timestamp_ms: int,
    horizon_ms: int,
    min_intervals: int = 8,
) -> int | None:
    """Estimate P(next update <= horizon | current age) from completed past intervals only."""
    if decision_timestamp_ms < 0 or horizon_ms <= 0 or min_intervals < 1:
        raise ValueError("invalid oracle hazard parameters")
    timestamps = sorted(set(int(value) for value in update_timestamps_ms))
    if any(value < 0 for value in timestamps):
        raise ValueError("oracle timestamps must be non-negative")
    if any(value > decision_timestamp_ms for value in timestamps):
        raise ValueError("future oracle update timestamp would leak target information")
    if not timestamps:
        return None
    intervals = [right - left for left, right in zip(timestamps, timestamps[1:]) if right > left]
    if len(intervals) < min_intervals:
        return None
    current_age = decision_timestamp_ms - timestamps[-1]
    at_risk = [interval for interval in intervals if interval >= current_age]
    if not at_risk:
        return None
    events = sum(1 for interval in at_risk if interval <= current_age + horizon_ms)
    return events * PPM // len(at_risk)


def _validate_observation(price: TimedPrice, *, decision_timestamp_ms: int) -> None:
    if price.price_e8 <= 0:
        raise ValueError(f"{price.source} price must be positive")
    if price.observed_at_ms < 0:
        raise ValueError(f"{price.source} timestamp must be non-negative")
    if price.observed_at_ms > decision_timestamp_ms:
        raise ValueError(f"{price.source} observation occurs after decision cutoff")


def build_alpha_feature_row(
    *,
    market: str,
    epoch: int,
    decision_timestamp_ms: int,
    chainlink: TimedPrice,
    spot: TimedPrice,
    perp: TimedPrice | None = None,
    spot_flow_imbalance_ppm: int | None = None,
    perp_flow_imbalance_ppm: int | None = None,
    oracle_update_timestamps_ms: Iterable[int] = (),
    oracle_hazard_horizon_ms: int = 5_000,
    oracle_hazard_min_intervals: int = 8,
) -> AlphaFeatureRow:
    if epoch < 0 or decision_timestamp_ms < 0:
        raise ValueError("epoch and decision timestamp must be non-negative")
    _validate_observation(chainlink, decision_timestamp_ms=decision_timestamp_ms)
    _validate_observation(spot, decision_timestamp_ms=decision_timestamp_ms)
    if perp is not None:
        _validate_observation(perp, decision_timestamp_ms=decision_timestamp_ms)
    for name, value in (
        ("spot_flow_imbalance_ppm", spot_flow_imbalance_ppm),
        ("perp_flow_imbalance_ppm", perp_flow_imbalance_ppm),
    ):
        if value is not None and not -PPM <= value <= PPM:
            raise ValueError(f"{name} must be in [-1_000_000, 1_000_000]")
    hazard = empirical_oracle_update_hazard_ppm(
        oracle_update_timestamps_ms,
        decision_timestamp_ms=decision_timestamp_ms,
        horizon_ms=oracle_hazard_horizon_ms,
        min_intervals=oracle_hazard_min_intervals,
    )
    return AlphaFeatureRow(
        market=market,
        epoch=epoch,
        decision_timestamp_ms=decision_timestamp_ms,
        chainlink_price_e8=chainlink.price_e8,
        chainlink_observed_at_ms=chainlink.observed_at_ms,
        oracle_age_ms=decision_timestamp_ms - chainlink.observed_at_ms,
        spot_price_e8=spot.price_e8,
        spot_observed_at_ms=spot.observed_at_ms,
        perp_price_e8=None if perp is None else perp.price_e8,
        perp_observed_at_ms=None if perp is None else perp.observed_at_ms,
        spot_oracle_gap_ppm=relative_gap_ppm(spot.price_e8, chainlink.price_e8),
        perp_oracle_gap_ppm=None if perp is None else relative_gap_ppm(perp.price_e8, chainlink.price_e8),
        spot_perp_basis_ppm=None if perp is None else relative_gap_ppm(perp.price_e8, spot.price_e8),
        spot_flow_imbalance_ppm=spot_flow_imbalance_ppm,
        perp_flow_imbalance_ppm=perp_flow_imbalance_ppm,
        oracle_update_hazard_ppm=hazard,
    )
