from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .backtest import BacktestConfig
from .binance import AggTrade
from .oracle_history import ActiveOracleHistory, build_active_oracle_history
from .replay import (
    ChainEvent,
    ReplaySnapshot,
    build_replay_snapshot,
    canonical_prediction_events,
)
from .research_dataset import ResearchDatasetBuildResult, build_research_dataset


def _event_order(event: ChainEvent) -> tuple[int, int, int, str]:
    return (
        event.block_number,
        event.tx_index,
        event.log_index,
        event.tx_hash,
    )


@dataclass(frozen=True, slots=True)
class CanonicalResearchInputs:
    market: str
    replay: ReplaySnapshot
    events: tuple[ChainEvent, ...]
    oracle_history: ActiveOracleHistory
    prediction_event_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "replay_rounds": len(self.replay.rounds),
            "replay_input_digest": self.replay.input_digest,
            "replay_output_digest": self.replay.output_digest,
            "prediction_event_count": self.prediction_event_count,
            "active_chainlink_event_count": len(self.oracle_history.events),
            "oracle_history": self.oracle_history.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CanonicalResearchDataset:
    inputs: CanonicalResearchInputs
    dataset: ResearchDatasetBuildResult

    def as_dict(self) -> dict[str, object]:
        return {
            "inputs": self.inputs.as_dict(),
            "dataset": self.dataset.as_dict(),
        }


def load_canonical_research_inputs(
    database: Path,
    market: str,
) -> CanonicalResearchInputs:
    replay = build_replay_snapshot(database, market)
    prediction_events = canonical_prediction_events(database, market)
    oracle_history = build_active_oracle_history(database, market)
    events = tuple(
        sorted(
            (*prediction_events, *oracle_history.events),
            key=_event_order,
        )
    )
    return CanonicalResearchInputs(
        market=market,
        replay=replay,
        events=events,
        oracle_history=oracle_history,
        prediction_event_count=len(prediction_events),
    )


def build_canonical_research_dataset(
    database: Path,
    market: str,
    spot_trades: tuple[AggTrade, ...],
    *,
    perp_trades: tuple[AggTrade, ...] = (),
    backtest_config: BacktestConfig | None = None,
    feature_lead_seconds: int = 20,
    flow_lookback_ms: int = 60_000,
    max_spot_age_ms: int = 5_000,
    max_perp_age_ms: int = 5_000,
    max_chainlink_age_ms: int | None = None,
    oracle_history_updates: int = 512,
    oracle_hazard_horizon_ms: int = 5_000,
    oracle_hazard_min_intervals: int = 8,
) -> CanonicalResearchDataset:
    inputs = load_canonical_research_inputs(database, market)
    dataset = build_research_dataset(
        inputs.replay,
        inputs.events,
        spot_trades,
        perp_trades=perp_trades,
        backtest_config=backtest_config,
        feature_lead_seconds=feature_lead_seconds,
        flow_lookback_ms=flow_lookback_ms,
        max_spot_age_ms=max_spot_age_ms,
        max_perp_age_ms=max_perp_age_ms,
        max_chainlink_age_ms=max_chainlink_age_ms,
        oracle_history_updates=oracle_history_updates,
        oracle_hazard_horizon_ms=oracle_hazard_horizon_ms,
        oracle_hazard_min_intervals=oracle_hazard_min_intervals,
    )
    return CanonicalResearchDataset(inputs=inputs, dataset=dataset)
