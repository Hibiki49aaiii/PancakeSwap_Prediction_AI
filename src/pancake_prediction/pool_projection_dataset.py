from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .backtest import BacktestConfig, build_event_index
from .economics import PPM
from .features import PoolFeatureRow, build_pool_feature_rows
from .replay import ChainEvent, ReplaySnapshot

POOL_FLOW_WINDOWS_SECONDS = (5, 20, 60)


@dataclass(frozen=True, slots=True)
class PoolProjectionFeatures:
    market: str
    epoch: int
    decision_timestamp: int
    scheduled_lock_timestamp: int
    observed_bull_wei: int
    observed_bear_wei: int
    observed_bull_share_ppm: int | None
    bet_count: int
    unique_bettors: int
    bull_flow_5s_wei: int
    bear_flow_5s_wei: int
    bull_flow_20s_wei: int
    bear_flow_20s_wei: int
    bull_flow_60s_wei: int
    bear_flow_60s_wei: int
    flow_imbalance_5s_ppm: int | None
    flow_imbalance_20s_ppm: int | None
    flow_imbalance_60s_ppm: int | None
    prior_bull_rate_20_ppm: int | None
    prior_abs_return_12_ppm: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PoolProjectionLabel:
    final_bull_wei: int
    final_bear_wei: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PoolProjectionDatasetRow:
    features: PoolProjectionFeatures
    label: PoolProjectionLabel

    def as_dict(self) -> dict[str, object]:
        return {
            "features": self.features.as_dict(),
            "label": self.label.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class PoolProjectionDataset:
    market: str
    feature_lead_seconds: int
    replay_digest: str
    rows: tuple[PoolProjectionDatasetRow, ...]
    dataset_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "feature_lead_seconds": self.feature_lead_seconds,
            "replay_digest": self.replay_digest,
            "row_count": len(self.rows),
            "dataset_digest": self.dataset_digest,
            "rows": [row.as_dict() for row in self.rows],
            "final_pool_is_label_only": True,
        }


def _imbalance_ppm(bull_wei: int, bear_wei: int) -> int | None:
    total = bull_wei + bear_wei
    if total <= 0:
        return None
    return (bull_wei - bear_wei) * PPM // total


def _window_flows(
    events: tuple[ChainEvent, ...],
    *,
    cutoff: int,
) -> dict[int, tuple[int, int]]:
    flows = {window: [0, 0] for window in POOL_FLOW_WINDOWS_SECONDS}
    for event in events:
        if event.block_timestamp >= cutoff:
            continue
        amount = event.decoded.get("amount")
        if not isinstance(amount, int) or amount < 0:
            continue
        age = cutoff - event.block_timestamp
        for window in POOL_FLOW_WINDOWS_SECONDS:
            if age > window:
                continue
            if event.event_name == "BetBull":
                flows[window][0] += amount
            elif event.event_name == "BetBear":
                flows[window][1] += amount
    return {window: (values[0], values[1]) for window, values in flows.items()}


def _features_from_pool_row(
    row: PoolFeatureRow,
    events: tuple[ChainEvent, ...],
) -> PoolProjectionFeatures:
    flows = _window_flows(events, cutoff=row.feature_timestamp)
    bull_5, bear_5 = flows[5]
    bull_20, bear_20 = flows[20]
    bull_60, bear_60 = flows[60]
    if bull_60 != row.last_60s_bull_wei or bear_60 != row.last_60s_bear_wei:
        raise ValueError("60-second pool flow disagrees with canonical pool feature row")
    return PoolProjectionFeatures(
        market=row.market,
        epoch=row.epoch,
        decision_timestamp=row.feature_timestamp,
        scheduled_lock_timestamp=row.scheduled_lock_timestamp,
        observed_bull_wei=row.bull_pool_wei,
        observed_bear_wei=row.bear_pool_wei,
        observed_bull_share_ppm=row.bull_share_ppm,
        bet_count=row.bet_count,
        unique_bettors=row.unique_bettors,
        bull_flow_5s_wei=bull_5,
        bear_flow_5s_wei=bear_5,
        bull_flow_20s_wei=bull_20,
        bear_flow_20s_wei=bear_20,
        bull_flow_60s_wei=bull_60,
        bear_flow_60s_wei=bear_60,
        flow_imbalance_5s_ppm=_imbalance_ppm(bull_5, bear_5),
        flow_imbalance_20s_ppm=_imbalance_ppm(bull_20, bear_20),
        flow_imbalance_60s_ppm=_imbalance_ppm(bull_60, bear_60),
        prior_bull_rate_20_ppm=row.prior_bull_rate_20_ppm,
        prior_abs_return_12_ppm=row.prior_abs_return_12_ppm,
    )


def _dataset_digest(
    *,
    market: str,
    feature_lead_seconds: int,
    replay_digest: str,
    rows: tuple[PoolProjectionDatasetRow, ...],
) -> str:
    payload = {
        "market": market,
        "feature_lead_seconds": feature_lead_seconds,
        "replay_digest": replay_digest,
        "rows": [row.as_dict() for row in rows],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((raw + "\n").encode()).hexdigest()


def build_pool_projection_dataset(
    replay: ReplaySnapshot,
    events: tuple[ChainEvent, ...],
    backtest_config: BacktestConfig,
    *,
    feature_lead_seconds: int = 20,
) -> PoolProjectionDataset:
    if feature_lead_seconds <= 0:
        raise ValueError("feature_lead_seconds must be positive")
    event_index = build_event_index(events)
    pool_rows = build_pool_feature_rows(
        replay,
        events,
        backtest_config,
        feature_lead_seconds=feature_lead_seconds,
    )
    by_epoch = {record.epoch: record for record in replay.rounds}
    result: list[PoolProjectionDatasetRow] = []
    for pool_row in pool_rows:
        record = by_epoch.get(pool_row.epoch)
        if record is None or record.label not in {"bull", "bear", "tie"}:
            continue
        epoch_events = event_index.bets_by_epoch.get(pool_row.epoch, ())
        features = _features_from_pool_row(pool_row, epoch_events)
        final_bull = record.bull_amount_wei
        final_bear = record.bear_amount_wei
        if final_bull < features.observed_bull_wei or final_bear < features.observed_bear_wei:
            raise ValueError("final pool is smaller than the decision-time observed pool")
        result.append(
            PoolProjectionDatasetRow(
                features=features,
                label=PoolProjectionLabel(
                    final_bull_wei=final_bull,
                    final_bear_wei=final_bear,
                ),
            )
        )
    rows = tuple(sorted(result, key=lambda row: row.features.epoch))
    digest = _dataset_digest(
        market=replay.market,
        feature_lead_seconds=feature_lead_seconds,
        replay_digest=replay.input_digest,
        rows=rows,
    )
    return PoolProjectionDataset(
        market=replay.market,
        feature_lead_seconds=feature_lead_seconds,
        replay_digest=replay.input_digest,
        rows=rows,
        dataset_digest=digest,
    )
