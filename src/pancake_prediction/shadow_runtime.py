from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .binance_archive import TimestampUnit
from .binance_live import (
    BinanceAggTradeSource,
    BinanceLiveCoverage,
    BinanceLiveSyncReport,
    inspect_binance_live_coverage,
    sync_binance_live_aggtrades,
)
from .clickhouse import ClickHouseJsonSink, ClickHouseParameterizedJsonSource
from .clickhouse_dataset import (
    ChunkedResearchDatasetBuildResult,
    build_chunked_clickhouse_research_dataset,
)
from .clickhouse_schema import inspect_binance_trade_schema
from .contracts import Market
from .research_inputs import load_canonical_research_inputs
from .shadow_campaign import (
    ShadowCampaignGateReport,
    ShadowCampaignPolicy,
    evaluate_shadow_campaign,
)
from .shadow_chain_sync import ShadowChainSyncReport, ShadowChainSyncRpc, sync_shadow_chain
from .shadow_inference import (
    ShadowInferenceConfig,
    ShadowInferenceResult,
    ShadowTargetSelection,
    build_shadow_inference,
    required_shadow_feature_epochs,
    select_shadow_target,
)
from .shadow_ledger import ShadowLedgerEvent, ShadowLedgerStore
from .shadow_reconciliation import ShadowReconciliationReport, reconcile_shadow_settlements


class ShadowRuntimeClickHouse(
    ClickHouseJsonSink,
    ClickHouseParameterizedJsonSource,
    Protocol,
):
    pass


@dataclass(frozen=True, slots=True)
class ShadowRuntimeConfig:
    chain_confirmations: int = 3
    chain_chunk_size: int = 2_000
    chain_reorg_lookback: int = 64
    spot_timestamp_unit: TimestampUnit = "auto"
    spot_availability_lag_ms: int = 250
    perp_timestamp_unit: TimestampUnit = "milliseconds"
    perp_availability_lag_ms: int = 250
    include_perp: bool = True
    binance_bootstrap_window_ms: int = 120_000
    binance_batch_size: int = 5_000
    binance_max_pages: int = 100
    dataset_chunk_span_ms: int = 3_600_000
    flow_lookback_ms: int = 60_000
    max_spot_age_ms: int = 5_000
    max_perp_age_ms: int = 5_000
    max_chainlink_age_ms: int | None = 300_000
    chainlink_availability_lag_ms: int = 1_000
    oracle_history_updates: int = 512
    oracle_hazard_horizon_ms: int = 5_000
    oracle_hazard_min_intervals: int = 8
    inference: ShadowInferenceConfig = field(default_factory=ShadowInferenceConfig)
    campaign_policy: ShadowCampaignPolicy = field(default_factory=ShadowCampaignPolicy)

    def validate(self) -> None:
        if self.chain_confirmations < 0:
            raise ValueError("chain_confirmations must be non-negative")
        if self.chain_chunk_size < 1:
            raise ValueError("chain_chunk_size must be positive")
        if self.chain_reorg_lookback < 1:
            raise ValueError("chain_reorg_lookback must be positive")
        if self.spot_availability_lag_ms < 0 or self.perp_availability_lag_ms < 0:
            raise ValueError("Binance availability lags must be non-negative")
        if self.binance_bootstrap_window_ms <= 0:
            raise ValueError("binance_bootstrap_window_ms must be positive")
        if self.binance_batch_size < 1 or self.binance_max_pages < 1:
            raise ValueError("Binance batch/page limits must be positive")
        if self.dataset_chunk_span_ms <= 0 or self.flow_lookback_ms <= 0:
            raise ValueError("dataset time windows must be positive")
        if self.max_spot_age_ms < 0 or self.max_perp_age_ms < 0:
            raise ValueError("Binance max age values must be non-negative")
        if self.max_chainlink_age_ms is not None and self.max_chainlink_age_ms < 0:
            raise ValueError("max_chainlink_age_ms must be non-negative")
        if self.chainlink_availability_lag_ms < 0:
            raise ValueError("chainlink_availability_lag_ms must be non-negative")
        if self.oracle_history_updates < self.oracle_hazard_min_intervals + 1:
            raise ValueError(
                "oracle_history_updates must cover oracle_hazard_min_intervals"
            )
        self.inference.validate()
        self.campaign_policy.validate()


@dataclass(frozen=True, slots=True)
class ShadowRuntimeCycleReport:
    market: str
    cycle_started_timestamp_ms: int
    selection_timestamp_ms: int
    completion_timestamp_ms: int
    status: str
    chain_sync: ShadowChainSyncReport
    spot_sync: BinanceLiveSyncReport
    spot_coverage: BinanceLiveCoverage
    perp_sync: BinanceLiveSyncReport | None
    perp_coverage: BinanceLiveCoverage | None
    reconciliation: ShadowReconciliationReport
    target: ShadowTargetSelection | None
    dataset: ChunkedResearchDatasetBuildResult | None
    inference: ShadowInferenceResult | None
    ledger_event: ShadowLedgerEvent | None
    campaign: ShadowCampaignGateReport
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "cycle_started_timestamp_ms": self.cycle_started_timestamp_ms,
            "selection_timestamp_ms": self.selection_timestamp_ms,
            "completion_timestamp_ms": self.completion_timestamp_ms,
            "status": self.status,
            "reason": self.reason,
            "chain_sync": self.chain_sync.as_dict(),
            "spot_sync": self.spot_sync.as_dict(),
            "spot_coverage": self.spot_coverage.as_dict(),
            "perp_sync": None if self.perp_sync is None else self.perp_sync.as_dict(),
            "perp_coverage": (
                None if self.perp_coverage is None else self.perp_coverage.as_dict()
            ),
            "reconciliation": self.reconciliation.as_dict(),
            "target": None if self.target is None else self.target.as_dict(),
            "dataset": None if self.dataset is None else self.dataset.as_dict(),
            "inference": None if self.inference is None else self.inference.as_dict(),
            "ledger_event": (
                None if self.ledger_event is None else self.ledger_event.as_dict()
            ),
            "campaign": self.campaign.as_dict(),
            "signing_enabled": False,
            "live_broadcast": False,
            "funded_execution": False,
            "profitability_gate_eligible": False,
        }


def _clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _live_warmup_ready(
    coverage: BinanceLiveCoverage,
    *,
    now_timestamp_ms: int,
    flow_lookback_ms: int,
) -> bool:
    first = coverage.first_available_at_ms
    return (
        first is not None
        and coverage.row_count > 0
        and now_timestamp_ms >= first + flow_lookback_ms
    )


def _finish_report(
    *,
    market: Market,
    started_ms: int,
    selection_ms: int,
    completion_ms: int,
    status: str,
    chain_sync: ShadowChainSyncReport,
    spot_sync: BinanceLiveSyncReport,
    spot_coverage: BinanceLiveCoverage,
    perp_sync: BinanceLiveSyncReport | None,
    perp_coverage: BinanceLiveCoverage | None,
    reconciliation: ShadowReconciliationReport,
    target: ShadowTargetSelection | None,
    dataset: ChunkedResearchDatasetBuildResult | None,
    inference: ShadowInferenceResult | None,
    ledger_event: ShadowLedgerEvent | None,
    shadow_store: ShadowLedgerStore,
    policy: ShadowCampaignPolicy,
    purge_rounds: int,
    reason: str | None = None,
) -> ShadowRuntimeCycleReport:
    campaign = evaluate_shadow_campaign(
        shadow_store.audit(purge_rounds=purge_rounds),
        policy,
    )
    return ShadowRuntimeCycleReport(
        market=market.symbol,
        cycle_started_timestamp_ms=started_ms,
        selection_timestamp_ms=selection_ms,
        completion_timestamp_ms=completion_ms,
        status=status,
        chain_sync=chain_sync,
        spot_sync=spot_sync,
        spot_coverage=spot_coverage,
        perp_sync=perp_sync,
        perp_coverage=perp_coverage,
        reconciliation=reconciliation,
        target=target,
        dataset=dataset,
        inference=inference,
        ledger_event=ledger_event,
        campaign=campaign,
        reason=reason,
    )


def run_shadow_runtime_cycle(
    rpc: ShadowChainSyncRpc,
    clickhouse: ShadowRuntimeClickHouse,
    binance_rest: BinanceAggTradeSource,
    market: Market,
    canonical_database: Path,
    shadow_database: Path,
    *,
    config: ShadowRuntimeConfig | None = None,
    now_timestamp_ms: int | None = None,
    completion_timestamp_ms: int | None = None,
) -> ShadowRuntimeCycleReport:
    selected = config or ShadowRuntimeConfig()
    selected.validate()
    started_ms = _clock_ms() if now_timestamp_ms is None else now_timestamp_ms
    if started_ms < 0:
        raise ValueError("now_timestamp_ms must be non-negative")

    schema = inspect_binance_trade_schema(clickhouse)
    if not schema.ready:
        raise ValueError(
            "ClickHouse binance_agg_trades schema is not retry-safe for Stage 4"
        )

    chain_report = sync_shadow_chain(
        rpc,
        market,
        canonical_database,
        confirmations=selected.chain_confirmations,
        chunk_size=selected.chain_chunk_size,
        reorg_lookback=selected.chain_reorg_lookback,
    )
    spot_report = sync_binance_live_aggtrades(
        clickhouse,
        clickhouse,
        binance_rest,
        market=market.symbol,
        venue="spot",
        availability_lag_ms=selected.spot_availability_lag_ms,
        timestamp_unit=selected.spot_timestamp_unit,
        now_timestamp_ms=started_ms,
        bootstrap_window_ms=selected.binance_bootstrap_window_ms,
        batch_size=selected.binance_batch_size,
        max_pages=selected.binance_max_pages,
    )
    perp_report: BinanceLiveSyncReport | None = None
    if selected.include_perp:
        perp_report = sync_binance_live_aggtrades(
            clickhouse,
            clickhouse,
            binance_rest,
            market=market.symbol,
            venue="um_futures",
            availability_lag_ms=selected.perp_availability_lag_ms,
            timestamp_unit=selected.perp_timestamp_unit,
            now_timestamp_ms=started_ms,
            bootstrap_window_ms=selected.binance_bootstrap_window_ms,
            batch_size=selected.binance_batch_size,
            max_pages=selected.binance_max_pages,
        )

    spot_coverage = inspect_binance_live_coverage(
        clickhouse,
        market=market.symbol,
        venue="spot",
        availability_lag_ms=selected.spot_availability_lag_ms,
        timestamp_unit=selected.spot_timestamp_unit,
    )
    perp_coverage: BinanceLiveCoverage | None = None
    if selected.include_perp:
        perp_coverage = inspect_binance_live_coverage(
            clickhouse,
            market=market.symbol,
            venue="um_futures",
            availability_lag_ms=selected.perp_availability_lag_ms,
            timestamp_unit=selected.perp_timestamp_unit,
        )

    inputs = load_canonical_research_inputs(canonical_database, market.symbol)
    shadow_store = ShadowLedgerStore(shadow_database)
    shadow_store.initialize()
    reconciliation = reconcile_shadow_settlements(shadow_store, inputs.replay)

    selection_ms = _clock_ms() if now_timestamp_ms is None else now_timestamp_ms
    spot_ready = _live_warmup_ready(
        spot_coverage,
        now_timestamp_ms=selection_ms,
        flow_lookback_ms=selected.flow_lookback_ms,
    )
    perp_ready = (
        True
        if perp_coverage is None
        else _live_warmup_ready(
            perp_coverage,
            now_timestamp_ms=selection_ms,
            flow_lookback_ms=selected.flow_lookback_ms,
        )
    )
    if not spot_ready or not perp_ready:
        pending_sources = [
            source
            for source, ready in (("spot", spot_ready), ("perp", perp_ready))
            if not ready
        ]
        completion_ms = (
            _clock_ms()
            if completion_timestamp_ms is None
            else completion_timestamp_ms
        )
        return _finish_report(
            market=market,
            started_ms=started_ms,
            selection_ms=selection_ms,
            completion_ms=completion_ms,
            status="source_warmup",
            chain_sync=chain_report,
            spot_sync=spot_report,
            spot_coverage=spot_coverage,
            perp_sync=perp_report,
            perp_coverage=perp_coverage,
            reconciliation=reconciliation,
            target=None,
            dataset=None,
            inference=None,
            ledger_event=None,
            shadow_store=shadow_store,
            policy=selected.campaign_policy,
            purge_rounds=selected.inference.purge_rounds,
            reason="prospective live warmup incomplete: " + ",".join(pending_sources),
        )

    target = select_shadow_target(
        inputs.replay,
        inputs.events,
        now_timestamp=selection_ms // 1_000,
        config=selected.inference,
    )
    if target is None:
        completion_ms = (
            _clock_ms()
            if completion_timestamp_ms is None
            else completion_timestamp_ms
        )
        return _finish_report(
            market=market,
            started_ms=started_ms,
            selection_ms=selection_ms,
            completion_ms=completion_ms,
            status="no_eligible_target",
            chain_sync=chain_report,
            spot_sync=spot_report,
            spot_coverage=spot_coverage,
            perp_sync=perp_report,
            perp_coverage=perp_coverage,
            reconciliation=reconciliation,
            target=None,
            dataset=None,
            inference=None,
            ledger_event=None,
            shadow_store=shadow_store,
            policy=selected.campaign_policy,
            purge_rounds=selected.inference.purge_rounds,
        )

    required_epochs = required_shadow_feature_epochs(
        inputs.replay,
        inputs.events,
        target_epoch=target.epoch,
        config=selected.inference,
    )

    dataset = build_chunked_clickhouse_research_dataset(
        inputs.replay,
        inputs.events,
        clickhouse,
        spot_availability_lag_ms=selected.spot_availability_lag_ms,
        spot_timestamp_unit=selected.spot_timestamp_unit,
        perp_availability_lag_ms=selected.perp_availability_lag_ms,
        perp_timestamp_unit=selected.perp_timestamp_unit,
        include_perp=selected.include_perp,
        chunk_span_ms=selected.dataset_chunk_span_ms,
        backtest_config=selected.inference.backtest_config(),
        feature_lead_seconds=selected.inference.decision_lead_seconds,
        flow_lookback_ms=selected.flow_lookback_ms,
        max_spot_age_ms=selected.max_spot_age_ms,
        max_perp_age_ms=selected.max_perp_age_ms,
        max_chainlink_age_ms=selected.max_chainlink_age_ms,
        chainlink_availability_lag_ms=selected.chainlink_availability_lag_ms,
        oracle_history_updates=selected.oracle_history_updates,
        oracle_hazard_horizon_ms=selected.oracle_hazard_horizon_ms,
        oracle_hazard_min_intervals=selected.oracle_hazard_min_intervals,
        required_epochs=required_epochs,
    )
    try:
        inference = build_shadow_inference(
            inputs.replay,
            inputs.events,
            dataset.dataset.research_feature_rows,
            target_epoch=target.epoch,
            config=selected.inference,
        )
    except ValueError as exc:
        completion_ms = (
            _clock_ms()
            if completion_timestamp_ms is None
            else completion_timestamp_ms
        )
        return _finish_report(
            market=market,
            started_ms=started_ms,
            selection_ms=selection_ms,
            completion_ms=completion_ms,
            status="target_not_ready",
            chain_sync=chain_report,
            spot_sync=spot_report,
            spot_coverage=spot_coverage,
            perp_sync=perp_report,
            perp_coverage=perp_coverage,
            reconciliation=reconciliation,
            target=target,
            dataset=dataset,
            inference=None,
            ledger_event=None,
            shadow_store=shadow_store,
            policy=selected.campaign_policy,
            purge_rounds=selected.inference.purge_rounds,
            reason=str(exc),
        )

    completion_ms = (
        _clock_ms()
        if completion_timestamp_ms is None
        else completion_timestamp_ms
    )
    if completion_ms < 0:
        raise ValueError("completion_timestamp_ms must be non-negative")
    if completion_ms // 1_000 >= target.latest_submission_timestamp:
        return _finish_report(
            market=market,
            started_ms=started_ms,
            selection_ms=selection_ms,
            completion_ms=completion_ms,
            status="missed_submission_deadline",
            chain_sync=chain_report,
            spot_sync=spot_report,
            spot_coverage=spot_coverage,
            perp_sync=perp_report,
            perp_coverage=perp_coverage,
            reconciliation=reconciliation,
            target=target,
            dataset=dataset,
            inference=inference,
            ledger_event=None,
            shadow_store=shadow_store,
            policy=selected.campaign_policy,
            purge_rounds=selected.inference.purge_rounds,
        )

    ledger_event = shadow_store.append_prediction(
        inference.prediction,
        purge_rounds=selected.inference.purge_rounds,
    )
    return _finish_report(
        market=market,
        started_ms=started_ms,
        selection_ms=selection_ms,
        completion_ms=completion_ms,
        status="prediction_recorded",
        chain_sync=chain_report,
        spot_sync=spot_report,
        spot_coverage=spot_coverage,
        perp_sync=perp_report,
        perp_coverage=perp_coverage,
        reconciliation=reconciliation,
        target=target,
        dataset=dataset,
        inference=inference,
        ledger_event=ledger_event,
        shadow_store=shadow_store,
        policy=selected.campaign_policy,
        purge_rounds=selected.inference.purge_rounds,
    )
