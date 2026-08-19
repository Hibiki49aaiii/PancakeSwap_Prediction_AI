from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .event_store import EventRecord, EventStore
from .historical_dataset import DecisionSnapshotBackfillResult, DecisionSnapshotPoint
from .historical_evidence_run import build_historical_binance_windows
from .historical_pipeline import HistoricalPipeline, HistoricalPipelineConfig
from .historical_public_rpc import (
    PredictionStaticConfig,
    _oracle_round_at_or_before_cutoff,
    count_relevant_prediction_config_changes,
    read_current_prediction_static_config,
)
from .pancake_contract import BNB_PREDICTION_CONTRACT, PredictionRoundState
from .prediction_subgraph import (
    PREDICTION_V2_SUBGRAPH_ID,
    PredictionSubgraphBet,
    PredictionSubgraphClient,
    PredictionSubgraphMeta,
    PredictionSubgraphRound,
)
from .provenance import ReconstructionPolicy, reconstruct_event
from .read_only_rpc import ReadOnlyJsonRpcClient
from .round_history import RoundTimeline, build_round_timelines
from .sources.chainlink import normalize_latest_round_data
from .sources.pancake import normalize_round_snapshot


_PRICE_SCALE = Decimal(10**8)


@dataclass(frozen=True, slots=True)
class SubgraphConfigProof:
    from_block: int
    through_block: int
    config_change_logs: int
    static_config: PredictionStaticConfig

    def validate(self) -> None:
        self.static_config.validate()
        if self.from_block < 0 or self.through_block < self.from_block:
            raise ValueError("invalid subgraph config proof range")
        if self.through_block != self.static_config.head_block:
            raise ValueError("config proof must extend through captured static-config head")
        if self.config_change_logs != 0:
            raise ValueError("config proof contains Prediction configuration changes")


@dataclass(frozen=True, slots=True)
class SubgraphHistoricalEvidenceRunResult:
    dataset_id: str
    meta: PredictionSubgraphMeta
    config_proof: SubgraphConfigProof
    rounds_fetched: int
    completed_rounds: int
    indexed_bets: int
    decision_snapshots: DecisionSnapshotBackfillResult
    binance_windows: int
    binance_events: int
    examples: int
    skipped_examples: int
    store_event_count: int
    store_tip_hash: str


def _price_e8(value: Decimal, field: str) -> int:
    scaled = value * _PRICE_SCALE
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"{field} has precision beyond Prediction event price scale")
    return int(integral)


def _expected_position(lock_price: Decimal, close_price: Decimal) -> str:
    if close_price > lock_price:
        return "Bull"
    if close_price < lock_price:
        return "Bear"
    return "House"


def _validate_complete_round(
    round_: PredictionSubgraphRound,
    *,
    interval_seconds: int,
    meta_block: int,
) -> None:
    if not round_.complete:
        raise ValueError(f"subgraph round {round_.epoch} is incomplete")
    assert round_.lock_at_s is not None
    assert round_.lock_block is not None
    assert round_.lock_hash is not None
    assert round_.lock_price is not None
    assert round_.lock_round_id is not None
    assert round_.close_at_s is not None
    assert round_.close_block is not None
    assert round_.close_hash is not None
    assert round_.close_price is not None
    assert round_.close_round_id is not None
    scheduled_lock = round_.start_at_s + interval_seconds
    scheduled_close = round_.start_at_s + 2 * interval_seconds
    if round_.lock_at_s < scheduled_lock:
        raise ValueError(f"subgraph round {round_.epoch} locked before configured schedule")
    if round_.close_at_s < scheduled_close:
        raise ValueError(f"subgraph round {round_.epoch} closed before configured schedule")
    if not (round_.start_block <= round_.lock_block <= round_.close_block <= meta_block):
        raise ValueError(f"subgraph round {round_.epoch} block order/indexing boundary is invalid")
    expected = _expected_position(round_.lock_price, round_.close_price)
    if round_.position != expected:
        raise ValueError(
            f"subgraph round {round_.epoch} result mismatch: position={round_.position} expected={expected}"
        )


def _raw_lifecycle_event(
    round_: PredictionSubgraphRound,
    kind: str,
    *,
    captured_at_ns: int,
) -> EventRecord:
    if kind == "START":
        timestamp_s = round_.start_at_s
        block_number = round_.start_block
        tx_hash = round_.start_hash
        oracle_round_id = None
        price = None
    elif kind == "LOCK":
        if None in (round_.lock_at_s, round_.lock_block, round_.lock_hash, round_.lock_price, round_.lock_round_id):
            raise ValueError(f"round {round_.epoch} lacks LOCK fields")
        timestamp_s = int(round_.lock_at_s)
        block_number = int(round_.lock_block)
        tx_hash = str(round_.lock_hash)
        oracle_round_id = int(round_.lock_round_id)
        price = _price_e8(round_.lock_price, "lockPrice")  # type: ignore[arg-type]
    elif kind == "END":
        if None in (round_.close_at_s, round_.close_block, round_.close_hash, round_.close_price, round_.close_round_id):
            raise ValueError(f"round {round_.epoch} lacks END fields")
        timestamp_s = int(round_.close_at_s)
        block_number = int(round_.close_block)
        tx_hash = str(round_.close_hash)
        oracle_round_id = int(round_.close_round_id)
        price = _price_e8(round_.close_price, "closePrice")  # type: ignore[arg-type]
    else:
        raise ValueError(f"unsupported lifecycle kind: {kind}")
    return EventRecord(
        event_id=f"pancake:prediction:subgraph:lifecycle:{round_.epoch}:{kind}:{tx_hash}",
        source="pancake_prediction",
        topic="prediction.round_lifecycle",
        event_time_ns=timestamp_s * 1_000_000_000,
        observed_at_ns=captured_at_ns,
        payload={
            "contract_address": BNB_PREDICTION_CONTRACT,
            "kind": kind,
            "epoch": round_.epoch,
            "block_number": block_number,
            "block_timestamp_s": timestamp_s,
            "transaction_hash": tx_hash,
            "oracle_round_id": oracle_round_id,
            "price": price,
            "indexed_source": {
                "provider": "the_graph",
                "subgraph_id": PREDICTION_V2_SUBGRAPH_ID,
                "entity": "Round",
                "entity_id": round_.id,
            },
        },
    )


def backfill_subgraph_lifecycle(
    store: EventStore,
    rounds: Iterable[PredictionSubgraphRound],
    *,
    dataset_id: str,
    interval_seconds: int,
    assumed_subgraph_latency_ns: int,
    captured_at_ns: int,
    meta_block: int,
) -> int:
    if store.mode != "reconstructed":
        raise ValueError("subgraph lifecycle backfill requires reconstructed Event Store")
    if assumed_subgraph_latency_ns < 0:
        raise ValueError("assumed_subgraph_latency_ns must be non-negative")
    existing = {item.event.event_id for item in store.read_all_ingest_order()}
    batch: list[EventRecord] = []
    for round_ in rounds:
        _validate_complete_round(round_, interval_seconds=interval_seconds, meta_block=meta_block)
        for kind in ("START", "LOCK", "END"):
            raw = _raw_lifecycle_event(round_, kind, captured_at_ns=captured_at_ns)
            reconstructed = reconstruct_event(
                raw,
                policy=ReconstructionPolicy(
                    dataset_id=dataset_id,
                    assumed_latency_ns=assumed_subgraph_latency_ns,
                    captured_at_ns=captured_at_ns,
                ),
                availability_base_ns=raw.event_time_ns,
                availability_basis="prediction_subgraph_block_timestamp",
            )
            if reconstructed.event_id not in existing:
                batch.append(reconstructed)
                existing.add(reconstructed.event_id)
    if batch:
        store.append_many(batch)
    return len(batch)


def _raw_bet_event(bet: PredictionSubgraphBet, *, captured_at_ns: int) -> EventRecord:
    return EventRecord(
        event_id=f"pancake:prediction:subgraph:bet:{bet.id}",
        source="pancake_prediction",
        topic="prediction.bet_indexed",
        event_time_ns=bet.created_at_s * 1_000_000_000,
        observed_at_ns=captured_at_ns,
        payload={
            "epoch": bet.epoch,
            "user": bet.user,
            "transaction_hash": bet.transaction_hash,
            "amount_wei": bet.amount_wei,
            "position": bet.position,
            "block_number": bet.block_number,
            "block_timestamp_s": bet.created_at_s,
            "indexed_source": {
                "provider": "the_graph",
                "subgraph_id": PREDICTION_V2_SUBGRAPH_ID,
                "entity": "Bet",
                "entity_id": bet.id,
            },
        },
    )


def _validate_round_bets(round_: PredictionSubgraphRound, bets: tuple[PredictionSubgraphBet, ...]) -> None:
    if any(bet.epoch != round_.epoch for bet in bets):
        raise ValueError(f"subgraph bet epoch mismatch for round {round_.epoch}")
    if any(bet.created_at_s < round_.start_at_s for bet in bets):
        raise ValueError(f"subgraph bet predates round {round_.epoch} start")
    if round_.lock_at_s is not None and any(bet.created_at_s > round_.lock_at_s for bet in bets):
        raise ValueError(f"subgraph bet occurs after round {round_.epoch} LockRound")
    bull = tuple(bet for bet in bets if bet.position == "Bull")
    bear = tuple(bet for bet in bets if bet.position == "Bear")
    if len(bets) != round_.total_bets or len(bull) != round_.bull_bets or len(bear) != round_.bear_bets:
        raise ValueError(f"subgraph bet counts do not reconcile for round {round_.epoch}")
    if sum(bet.amount_wei for bet in bets) != round_.total_amount_wei:
        raise ValueError(f"subgraph total bet amount does not reconcile for round {round_.epoch}")
    if sum(bet.amount_wei for bet in bull) != round_.bull_amount_wei:
        raise ValueError(f"subgraph bull amount does not reconcile for round {round_.epoch}")
    if sum(bet.amount_wei for bet in bear) != round_.bear_amount_wei:
        raise ValueError(f"subgraph bear amount does not reconcile for round {round_.epoch}")


def backfill_subgraph_bets(
    store: EventStore,
    rounds: Iterable[PredictionSubgraphRound],
    bets_by_epoch: Mapping[int, tuple[PredictionSubgraphBet, ...]],
    *,
    dataset_id: str,
    assumed_subgraph_latency_ns: int,
    captured_at_ns: int,
    meta_block: int,
) -> int:
    if store.mode != "reconstructed":
        raise ValueError("subgraph bet backfill requires reconstructed Event Store")
    existing = {item.event.event_id for item in store.read_all_ingest_order()}
    batch: list[EventRecord] = []
    for round_ in rounds:
        bets = bets_by_epoch.get(round_.epoch, ())
        _validate_round_bets(round_, bets)
        for bet in bets:
            if bet.block_number > meta_block:
                raise ValueError("subgraph bet exceeds indexed meta block")
            raw = _raw_bet_event(bet, captured_at_ns=captured_at_ns)
            reconstructed = reconstruct_event(
                raw,
                policy=ReconstructionPolicy(
                    dataset_id=dataset_id,
                    assumed_latency_ns=assumed_subgraph_latency_ns,
                    captured_at_ns=captured_at_ns,
                ),
                availability_base_ns=raw.event_time_ns,
                availability_basis="prediction_subgraph_block_timestamp",
            )
            if reconstructed.event_id not in existing:
                batch.append(reconstructed)
                existing.add(reconstructed.event_id)
    if batch:
        store.append_many(batch)
    return len(batch)


def _bet_digest(bets: Iterable[PredictionSubgraphBet]) -> str:
    payload = [
        {
            "id": bet.id,
            "epoch": bet.epoch,
            "user": bet.user,
            "transaction_hash": bet.transaction_hash,
            "amount_wei": bet.amount_wei,
            "position": bet.position,
            "created_at_s": bet.created_at_s,
            "block_number": bet.block_number,
        }
        for bet in bets
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def backfill_subgraph_decision_snapshots(
    rpc_client: ReadOnlyJsonRpcClient,
    store: EventStore,
    timelines: Iterable[RoundTimeline],
    rounds_by_epoch: Mapping[int, PredictionSubgraphRound],
    bets_by_epoch: Mapping[int, tuple[PredictionSubgraphBet, ...]],
    *,
    dataset_id: str,
    static_config: PredictionStaticConfig,
    decision_lead_ns: int,
    assumed_onchain_latency_ns: int,
    assumed_subgraph_latency_ns: int,
    max_oracle_backtrack_rounds: int = 512,
) -> DecisionSnapshotBackfillResult:
    if store.mode != "reconstructed":
        raise ValueError("subgraph decision backfill requires reconstructed Event Store")
    existing = {item.event.event_id for item in store.read_all_ingest_order()}
    points: list[DecisionSnapshotPoint] = []
    already_present: list[int] = []
    for timeline in timelines:
        round_ = rounds_by_epoch.get(timeline.epoch)
        if round_ is None:
            raise ValueError(f"missing subgraph round for epoch {timeline.epoch}")
        cutoff_ns = timeline.lock_timestamp_ns - decision_lead_ns
        if cutoff_ns <= timeline.start_event.event_time_ns:
            raise ValueError(f"decision cutoff for epoch {timeline.epoch} is not after round start")
        all_bets = bets_by_epoch.get(timeline.epoch, ())
        eligible = tuple(
            bet
            for bet in all_bets
            if bet.created_at_s * 1_000_000_000 + assumed_subgraph_latency_ns <= cutoff_ns
        )
        bull = sum(bet.amount_wei for bet in eligible if bet.position == "Bull")
        bear = sum(bet.amount_wei for bet in eligible if bet.position == "Bear")
        source_time_s = max(
            [round_.start_at_s, *(bet.created_at_s for bet in eligible)]
        )
        source_block = max(
            [round_.start_block, *(bet.block_number for bet in eligible)]
        )
        scheduled_lock_s = round_.start_at_s + static_config.interval_seconds
        state = PredictionRoundState(
            epoch=round_.epoch,
            start_timestamp=round_.start_at_s,
            lock_timestamp=scheduled_lock_s,
            close_timestamp=round_.start_at_s + 2 * static_config.interval_seconds,
            lock_price=0,
            close_price=0,
            lock_oracle_id=0,
            close_oracle_id=0,
            total_amount_wei=bull + bear,
            bull_amount_wei=bull,
            bear_amount_wei=bear,
            reward_base_cal_amount_wei=0,
            reward_amount_wei=0,
            oracle_called=False,
        )
        source_round = normalize_round_snapshot(
            state,
            contract_address=BNB_PREDICTION_CONTRACT,
            treasury_fee_units=static_config.treasury_fee_units,
            block_number=source_block,
            block_timestamp_s=source_time_s,
            observed_at_ns=static_config.captured_at_ns,
        )
        payload = dict(source_round.payload)
        payload["historical_reconstruction"] = {
            "method": "prediction_v2_subgraph_bets_v1",
            "subgraph_id": PREDICTION_V2_SUBGRAPH_ID,
            "static_config_head_block": static_config.head_block,
            "eligible_bet_count": len(eligible),
            "eligible_bet_sha256": _bet_digest(eligible),
            "snapshot_source_block": source_block,
            "snapshot_source_timestamp_s": source_time_s,
        }
        source_round = EventRecord(
            event_id=source_round.event_id,
            source=source_round.source,
            topic=source_round.topic,
            event_time_ns=source_round.event_time_ns,
            observed_at_ns=source_round.observed_at_ns,
            payload=payload,
        )
        if round_.lock_round_id is None:
            raise ValueError(f"round {round_.epoch} has no Chainlink lock round ID")
        oracle_values = _oracle_round_at_or_before_cutoff(
            rpc_client,
            oracle_address=static_config.oracle_address,
            starting_round_id=round_.lock_round_id,
            cutoff_ns=cutoff_ns,
            assumed_onchain_latency_ns=assumed_onchain_latency_ns,
            max_backtrack_rounds=max_oracle_backtrack_rounds,
        )
        source_chainlink = normalize_latest_round_data(
            oracle_values,
            decimals=static_config.oracle_decimals,
            feed_address=static_config.oracle_address,
            observed_at_ns=static_config.captured_at_ns,
            description=static_config.oracle_description,
        )
        reconstructed_round = reconstruct_event(
            source_round,
            policy=ReconstructionPolicy(
                dataset_id=dataset_id,
                assumed_latency_ns=assumed_subgraph_latency_ns,
                captured_at_ns=static_config.captured_at_ns,
            ),
            availability_base_ns=source_time_s * 1_000_000_000,
            availability_basis="prediction_subgraph_latest_eligible_bet_block",
        )
        reconstructed_chainlink = reconstruct_event(
            source_chainlink,
            policy=ReconstructionPolicy(
                dataset_id=dataset_id,
                assumed_latency_ns=assumed_onchain_latency_ns,
                captured_at_ns=static_config.captured_at_ns,
            ),
            availability_base_ns=int(oracle_values[3]) * 1_000_000_000,
            availability_basis="chainlink_updated_at",
        )
        if reconstructed_round.observed_at_ns > cutoff_ns:
            raise AssertionError("subgraph pool snapshot is unavailable by cutoff")
        if reconstructed_chainlink.observed_at_ns > cutoff_ns:
            raise AssertionError("Chainlink snapshot is unavailable by cutoff")
        if reconstructed_round.event_id in existing:
            already_present.append(timeline.epoch)
            continue
        stored = store.append_many((reconstructed_round, reconstructed_chainlink))
        existing.update(item.event.event_id for item in stored)
        points.append(
            DecisionSnapshotPoint(
                epoch=timeline.epoch,
                decision_cutoff_ns=cutoff_ns,
                block_number=source_block,
                block_timestamp_s=source_time_s,
                reconstructed_observed_at_ns=reconstructed_round.observed_at_ns,
            )
        )
    return DecisionSnapshotBackfillResult(
        points=tuple(points),
        already_present_epochs=tuple(already_present),
    )


def build_subgraph_config_proof(
    rpc_client: ReadOnlyJsonRpcClient,
    *,
    from_block: int,
    log_chunk_size: int = 5_000,
) -> SubgraphConfigProof:
    static_config = read_current_prediction_static_config(rpc_client)
    changes = count_relevant_prediction_config_changes(
        rpc_client,
        from_block=from_block,
        to_block=static_config.head_block,
        chunk_size=log_chunk_size,
    )
    proof = SubgraphConfigProof(
        from_block=from_block,
        through_block=static_config.head_block,
        config_change_logs=changes,
        static_config=static_config,
    )
    proof.validate()
    return proof


def run_subgraph_historical_evidence_acquisition(
    store: EventStore,
    *,
    config: HistoricalPipelineConfig,
    subgraph_client: PredictionSubgraphClient,
    binance_client: Any,
    rpc_client: ReadOnlyJsonRpcClient,
    from_epoch: int,
    to_epoch: int,
    assumed_subgraph_latency_ns: int,
    symbol: str = "BNBUSDT",
    subgraph_page_size: int = 500,
    config_log_chunk_size: int = 5_000,
    binance_batch_limit: int = 1_000,
    binance_max_batches_per_window: int = 10_000,
    max_oracle_backtrack_rounds: int = 512,
) -> SubgraphHistoricalEvidenceRunResult:
    if store.mode != "reconstructed":
        raise ValueError("subgraph historical acquisition requires reconstructed Event Store")
    config.validate()
    if from_epoch < 0 or to_epoch < from_epoch:
        raise ValueError("invalid subgraph epoch range")
    if assumed_subgraph_latency_ns < 0:
        raise ValueError("assumed_subgraph_latency_ns must be non-negative")
    if not store.verify_chain():
        raise ValueError("source Event Store hash chain verification failed before acquisition")

    meta = subgraph_client.meta()
    if meta.has_indexing_errors:
        raise ValueError("Prediction subgraph reports indexing errors")
    rounds = subgraph_client.rounds(
        from_epoch=from_epoch,
        to_epoch=to_epoch,
        page_size=subgraph_page_size,
    )
    complete = tuple(round_ for round_ in rounds if round_.complete)
    if not complete:
        raise ValueError("subgraph epoch range contains no completed Prediction rounds")
    if max(int(round_.close_block or 0) for round_ in complete) > meta.block_number:
        raise ValueError("subgraph round data exceeds indexed meta block")

    proof = build_subgraph_config_proof(
        rpc_client,
        from_block=min(round_.start_block for round_ in complete),
        log_chunk_size=config_log_chunk_size,
    )
    static_config = proof.static_config
    for round_ in complete:
        _validate_complete_round(
            round_,
            interval_seconds=static_config.interval_seconds,
            meta_block=meta.block_number,
        )

    effective_config = replace(
        config,
        prediction_interval_seconds=static_config.interval_seconds,
        assumed_subgraph_latency_ns=assumed_subgraph_latency_ns,
    )
    pipeline = HistoricalPipeline(store, effective_config)
    backfill_subgraph_lifecycle(
        store,
        complete,
        dataset_id=config.dataset_id,
        interval_seconds=static_config.interval_seconds,
        assumed_subgraph_latency_ns=assumed_subgraph_latency_ns,
        captured_at_ns=static_config.captured_at_ns,
        meta_block=meta.block_number,
    )
    timelines_result = build_round_timelines(
        (item.event for item in store.read_all_ingest_order()),
        interval_seconds=static_config.interval_seconds,
    )
    expected_epochs = {round_.epoch for round_ in complete}
    timelines = tuple(
        timeline for timeline in timelines_result.completed if timeline.epoch in expected_epochs
    )
    if len(timelines) != len(complete):
        raise ValueError("subgraph lifecycle reconstruction did not yield every complete round")

    bets_by_epoch: dict[int, tuple[PredictionSubgraphBet, ...]] = {}
    for round_ in complete:
        bets_by_epoch[round_.epoch] = subgraph_client.bets_for_round(
            round_.id,
            expected_epoch=round_.epoch,
            page_size=subgraph_page_size,
        )
        _validate_round_bets(round_, bets_by_epoch[round_.epoch])
    backfill_subgraph_bets(
        store,
        complete,
        bets_by_epoch,
        dataset_id=config.dataset_id,
        assumed_subgraph_latency_ns=assumed_subgraph_latency_ns,
        captured_at_ns=static_config.captured_at_ns,
        meta_block=meta.block_number,
    )

    windows = build_historical_binance_windows(timelines, effective_config)
    binance_results = []
    for window in windows:
        binance_results.append(
            pipeline.backfill_binance(
                binance_client,
                symbol=symbol,
                start_time_ms=window.start_time_ms,
                end_time_ms=window.end_time_ms,
                batch_limit=binance_batch_limit,
                max_batches=binance_max_batches_per_window,
            )
        )

    decision_snapshots = backfill_subgraph_decision_snapshots(
        rpc_client,
        store,
        timelines,
        {round_.epoch: round_ for round_ in complete},
        bets_by_epoch,
        dataset_id=config.dataset_id,
        static_config=static_config,
        decision_lead_ns=config.decision_lead_ns,
        assumed_onchain_latency_ns=config.assumed_onchain_latency_ns,
        assumed_subgraph_latency_ns=assumed_subgraph_latency_ns,
        max_oracle_backtrack_rounds=max_oracle_backtrack_rounds,
    )
    examples = pipeline.build_examples()
    events = store.read_all_ingest_order()
    if not events or not store.verify_chain():
        raise ValueError("reconstructed Event Store is empty or hash verification failed")
    return SubgraphHistoricalEvidenceRunResult(
        dataset_id=config.dataset_id,
        meta=meta,
        config_proof=proof,
        rounds_fetched=len(rounds),
        completed_rounds=len(complete),
        indexed_bets=sum(len(value) for value in bets_by_epoch.values()),
        decision_snapshots=decision_snapshots,
        binance_windows=len(windows),
        binance_events=sum(result.events_appended for result in binance_results),
        examples=len(examples.examples),
        skipped_examples=len(examples.skipped),
        store_event_count=len(events),
        store_tip_hash=events[-1].event_hash,
    )
