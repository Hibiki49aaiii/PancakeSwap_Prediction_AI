from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

from eth_hash.auto import keccak

from .abi_codec import decode_result, encode_call
from .event_store import EventRecord, EventStore
from .historical_dataset import DecisionSnapshotBackfillResult, DecisionSnapshotPoint
from .historical_evidence_run import build_historical_binance_windows
from .historical_pipeline import HistoricalPipeline, HistoricalPipelineConfig
from .pancake_contract import (
    BNB_CHAIN_ID,
    BNB_PREDICTION_CONTRACT,
    PredictionRoundState,
)
from .provenance import ReconstructionPolicy, reconstruct_event
from .read_only_rpc import ReadOnlyJsonRpcClient
from .round_history import RoundTimeline, build_round_timelines
from .rpc_snapshot import BlockAnchor, find_block_at_or_before_timestamp
from .sources.chainlink import normalize_latest_round_data
from .sources.pancake import normalize_oracle_reference, normalize_round_snapshot


_BET_BULL_TOPIC = "0x" + keccak(b"BetBull(address,uint256,uint256)").hex()
_BET_BEAR_TOPIC = "0x" + keccak(b"BetBear(address,uint256,uint256)").hex()
_RELEVANT_CONFIG_TOPICS = (
    "0x" + keccak(b"NewBufferAndIntervalSeconds(uint256,uint256)").hex(),
    "0x" + keccak(b"NewTreasuryFee(uint256,uint256)").hex(),
    "0x" + keccak(b"NewOracle(address)").hex(),
)


@dataclass(frozen=True, slots=True)
class PredictionStaticConfig:
    head_block: int
    interval_seconds: int
    treasury_fee_units: int
    oracle_address: str
    oracle_decimals: int
    oracle_description: str
    captured_at_ns: int

    def validate(self) -> None:
        if self.head_block <= 0:
            raise ValueError("config head block must be positive")
        if self.interval_seconds <= 0:
            raise ValueError("intervalSeconds must be positive")
        if not 0 <= self.treasury_fee_units <= 1_000:
            raise ValueError("treasuryFee outside Prediction contract bound")
        if not self.oracle_address.startswith("0x") or len(self.oracle_address) != 42:
            raise ValueError("oracle address is invalid")
        if not 0 <= self.oracle_decimals <= 36:
            raise ValueError("oracle decimals outside supported bound")
        if self.captured_at_ns < 0:
            raise ValueError("config capture time must be non-negative")


@dataclass(frozen=True, slots=True)
class PredictionBetLog:
    side: str
    epoch: int
    amount_wei: int
    sender: str
    block_number: int
    block_hash: str
    transaction_hash: str
    log_index: int

    def validate(self) -> None:
        if self.side not in {"BULL", "BEAR"}:
            raise ValueError("bet side must be BULL or BEAR")
        if self.epoch < 0 or self.amount_wei <= 0 or self.block_number < 0 or self.log_index < 0:
            raise ValueError("bet log numeric fields are invalid")
        if not self.sender.startswith("0x") or len(self.sender) != 42:
            raise ValueError("bet sender is invalid")
        for value, field in (
            (self.block_hash, "block_hash"),
            (self.transaction_hash, "transaction_hash"),
        ):
            if not value.startswith("0x") or len(value) != 66:
                raise ValueError(f"{field} is invalid")

    def canonical_payload(self) -> dict[str, object]:
        self.validate()
        return {
            "side": self.side,
            "epoch": self.epoch,
            "amount_wei": self.amount_wei,
            "sender": self.sender,
            "block_number": self.block_number,
            "block_hash": self.block_hash,
            "transaction_hash": self.transaction_hash,
            "log_index": self.log_index,
        }


@dataclass(frozen=True, slots=True)
class PublicRpcHistoricalEvidenceRunResult:
    dataset_id: str
    static_config: PredictionStaticConfig
    config_change_logs: int
    completed_rounds: int
    incomplete_epochs: tuple[int, ...]
    bet_logs: int
    decision_snapshots: DecisionSnapshotBackfillResult
    binance_windows: int
    binance_events: int
    examples: int
    skipped_examples: int
    store_event_count: int
    store_tip_hash: str


def _hex_int(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"{field} must be hex")
    return int(value, 16)


def _bytes32(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        raise ValueError(f"{field} must be 32-byte hex")
    int(value[2:], 16)
    return value.lower()


def _address_topic(value: object, field: str) -> str:
    raw = _bytes32(value, field)
    address = "0x" + raw[-40:]
    int(address[2:], 16)
    return address.lower()


def _call_latest(
    client: ReadOnlyJsonRpcClient,
    *,
    to: str,
    signature: str,
    output_types: tuple[str, ...],
    argument_types: tuple[str, ...] = (),
    arguments: tuple[object, ...] = (),
) -> tuple[object, ...]:
    raw = client.call(
        "eth_call",
        [
            {
                "to": to,
                "data": encode_call(
                    signature,
                    argument_types=argument_types,
                    arguments=arguments,
                ),
            },
            "latest",
        ],
    )
    if not isinstance(raw, str) or not raw.startswith("0x"):
        raise ValueError(f"{signature} returned invalid RPC data")
    return tuple(decode_result(raw, output_types))


def read_current_prediction_static_config(
    client: ReadOnlyJsonRpcClient,
    *,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    captured_at_ns: int | None = None,
) -> PredictionStaticConfig:
    """Read only configuration that may be projected backward after log proof.

    The caller must separately prove that no relevant configuration-change event
    occurred from the historical range start through this captured head.
    """

    if client.chain_id() != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}")
    head = client.block_number()
    interval_seconds = int(
        _call_latest(
            client,
            to=prediction_contract,
            signature="intervalSeconds()",
            output_types=("uint256",),
        )[0]
    )
    treasury_fee_units = int(
        _call_latest(
            client,
            to=prediction_contract,
            signature="treasuryFee()",
            output_types=("uint256",),
        )[0]
    )
    oracle_address = str(
        _call_latest(
            client,
            to=prediction_contract,
            signature="oracle()",
            output_types=("address",),
        )[0]
    ).lower()
    oracle_decimals = int(
        _call_latest(
            client,
            to=oracle_address,
            signature="decimals()",
            output_types=("uint8",),
        )[0]
    )
    oracle_description = str(
        _call_latest(
            client,
            to=oracle_address,
            signature="description()",
            output_types=("string",),
        )[0]
    )
    result = PredictionStaticConfig(
        head_block=head,
        interval_seconds=interval_seconds,
        treasury_fee_units=treasury_fee_units,
        oracle_address=oracle_address,
        oracle_decimals=oracle_decimals,
        oracle_description=oracle_description,
        captured_at_ns=time.time_ns() if captured_at_ns is None else captured_at_ns,
    )
    result.validate()
    return result


def _get_logs(
    client: ReadOnlyJsonRpcClient,
    *,
    prediction_contract: str,
    from_block: int,
    to_block: int,
    topics0: Iterable[str],
) -> list[dict[str, Any]]:
    result = client.call(
        "eth_getLogs",
        [
            {
                "address": prediction_contract,
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [list(topics0)],
            }
        ],
    )
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise ValueError("eth_getLogs must return an array of objects")
    return result


def count_relevant_prediction_config_changes(
    client: ReadOnlyJsonRpcClient,
    *,
    from_block: int,
    to_block: int,
    chunk_size: int = 5_000,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> int:
    """Count interval/treasury/oracle changes in a bounded public-log scan."""

    if from_block < 0 or to_block < from_block:
        raise ValueError("invalid config scan range")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if client.chain_id() != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}")
    count = 0
    start = from_block
    while start <= to_block:
        end = min(to_block, start + chunk_size - 1)
        count += len(
            _get_logs(
                client,
                prediction_contract=prediction_contract,
                from_block=start,
                to_block=end,
                topics0=_RELEVANT_CONFIG_TOPICS,
            )
        )
        start = end + 1
    return count


def collect_prediction_bet_logs(
    client: ReadOnlyJsonRpcClient,
    *,
    from_block: int,
    to_block: int,
    chunk_size: int = 5_000,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
) -> tuple[PredictionBetLog, ...]:
    """Collect BULL/BEAR pool contributions without requiring archive state."""

    if from_block < 0 or to_block < from_block:
        raise ValueError("invalid bet log range")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if client.chain_id() != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}")

    bets: list[PredictionBetLog] = []
    start = from_block
    while start <= to_block:
        end = min(to_block, start + chunk_size - 1)
        raw_logs = _get_logs(
            client,
            prediction_contract=prediction_contract,
            from_block=start,
            to_block=end,
            topics0=(_BET_BULL_TOPIC, _BET_BEAR_TOPIC),
        )
        for raw in raw_logs:
            if raw.get("removed") is True:
                raise ValueError("removed/reorged bet log cannot be reconstructed")
            topics = raw.get("topics")
            if not isinstance(topics, list) or len(topics) != 3:
                raise ValueError("bet log topics are incomplete")
            topic0 = str(topics[0]).lower()
            if topic0 == _BET_BULL_TOPIC.lower():
                side = "BULL"
            elif topic0 == _BET_BEAR_TOPIC.lower():
                side = "BEAR"
            else:
                raise ValueError("unexpected bet event topic")
            data = raw.get("data")
            if not isinstance(data, str) or not data.startswith("0x"):
                raise ValueError("bet log data must be hex")
            decoded = decode_result(data, ("uint256",))
            bet = PredictionBetLog(
                side=side,
                epoch=_hex_int(topics[2], "bet epoch"),
                amount_wei=int(decoded[0]),
                sender=_address_topic(topics[1], "bet sender"),
                block_number=_hex_int(raw.get("blockNumber"), "bet blockNumber"),
                block_hash=_bytes32(raw.get("blockHash"), "bet blockHash"),
                transaction_hash=_bytes32(raw.get("transactionHash"), "bet transactionHash"),
                log_index=_hex_int(raw.get("logIndex"), "bet logIndex"),
            )
            bet.validate()
            bets.append(bet)
        start = end + 1

    bets.sort(key=lambda item: (item.block_number, item.log_index, item.transaction_hash))
    identities = [(item.transaction_hash, item.log_index) for item in bets]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate bet log identity returned by RPC")
    return tuple(bets)


def _bet_digest(bets: Iterable[PredictionBetLog]) -> str:
    payload = [item.canonical_payload() for item in bets]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _oracle_round_at_or_before_cutoff(
    client: ReadOnlyJsonRpcClient,
    *,
    oracle_address: str,
    starting_round_id: int,
    cutoff_ns: int,
    assumed_onchain_latency_ns: int,
    max_backtrack_rounds: int,
) -> tuple[object, ...]:
    if starting_round_id <= 0:
        raise ValueError("starting Chainlink round ID must be positive")
    if cutoff_ns <= 0 or assumed_onchain_latency_ns < 0:
        raise ValueError("oracle cutoff/latency is invalid")
    if max_backtrack_rounds <= 0:
        raise ValueError("max_backtrack_rounds must be positive")

    for offset in range(max_backtrack_rounds + 1):
        round_id = starting_round_id - offset
        if round_id <= 0:
            break
        try:
            values = _call_latest(
                client,
                to=oracle_address,
                signature="getRoundData(uint80)",
                output_types=("uint80", "int256", "uint256", "uint256", "uint80"),
                argument_types=("uint80",),
                arguments=(round_id,),
            )
        except Exception:
            continue
        returned_round_id = int(values[0])
        updated_at_s = int(values[3])
        if returned_round_id != round_id or updated_at_s <= 0:
            continue
        available_at_ns = updated_at_s * 1_000_000_000 + assumed_onchain_latency_ns
        if available_at_ns <= cutoff_ns:
            return values
    raise ValueError("no Chainlink round available by decision cutoff within backtrack bound")


def _payload_block_number(event: EventRecord, field: str) -> int:
    value = event.payload.get("block_number")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} lifecycle block_number is invalid")
    return value


def _lock_oracle_round_id(timeline: RoundTimeline) -> int:
    value = timeline.lock_event.payload.get("oracle_round_id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"epoch {timeline.epoch} LockRound oracle round ID is unavailable")
    return value


def backfill_public_rpc_decision_snapshots(
    client: ReadOnlyJsonRpcClient,
    store: EventStore,
    timelines: Iterable[RoundTimeline],
    bets: Iterable[PredictionBetLog],
    *,
    dataset_id: str,
    static_config: PredictionStaticConfig,
    decision_lead_ns: int,
    assumed_onchain_latency_ns: int,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    max_oracle_backtrack_rounds: int = 512,
) -> DecisionSnapshotBackfillResult:
    """Reconstruct decision state from logs and current-state Chainlink history.

    No historical `eth_call` is used. The current static config is eligible only
    after the caller proved that no interval/treasury/oracle change occurred from
    the historical range start through `static_config.head_block`.
    """

    if store.mode != "reconstructed":
        raise ValueError("public RPC decision backfill requires reconstructed Event Store")
    if not dataset_id:
        raise ValueError("dataset_id is required")
    static_config.validate()
    if decision_lead_ns <= 0 or assumed_onchain_latency_ns < 0:
        raise ValueError("decision lead/latency is invalid")

    timeline_values = tuple(timelines)
    bet_values = tuple(bets)
    for bet in bet_values:
        bet.validate()
    by_epoch: dict[int, list[PredictionBetLog]] = {}
    for bet in bet_values:
        by_epoch.setdefault(bet.epoch, []).append(bet)

    existing = {item.event.event_id for item in store.read_all_ingest_order()}
    points: list[DecisionSnapshotPoint] = []
    already_present: list[int] = []
    for timeline in timeline_values:
        cutoff_ns = timeline.lock_timestamp_ns - decision_lead_ns
        if cutoff_ns <= timeline.start_event.event_time_ns:
            raise ValueError(f"decision cutoff for epoch {timeline.epoch} is not after round start")
        latest_block_time_ns = cutoff_ns - assumed_onchain_latency_ns
        if latest_block_time_ns <= timeline.start_event.event_time_ns:
            raise ValueError(f"latency leaves no on-chain decision state for epoch {timeline.epoch}")

        start_block = _payload_block_number(timeline.start_event, "start")
        lock_block = _payload_block_number(timeline.lock_event, "lock")
        anchor = find_block_at_or_before_timestamp(
            client,
            target_timestamp_s=latest_block_time_ns // 1_000_000_000,
            lower_block=start_block,
            upper_block=lock_block,
        )
        scheduled_lock_s = timeline.lock_timestamp_ns // 1_000_000_000
        start_timestamp_s = timeline.start_event.event_time_ns // 1_000_000_000
        if scheduled_lock_s != start_timestamp_s + static_config.interval_seconds:
            raise ValueError(f"epoch {timeline.epoch} schedule is inconsistent with static interval")

        contributing = tuple(
            item
            for item in by_epoch.get(timeline.epoch, ())
            if item.block_number <= anchor.number
        )
        bull = sum(item.amount_wei for item in contributing if item.side == "BULL")
        bear = sum(item.amount_wei for item in contributing if item.side == "BEAR")
        state = PredictionRoundState(
            epoch=timeline.epoch,
            start_timestamp=start_timestamp_s,
            lock_timestamp=scheduled_lock_s,
            close_timestamp=start_timestamp_s + 2 * static_config.interval_seconds,
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
            contract_address=prediction_contract,
            treasury_fee_units=static_config.treasury_fee_units,
            block_number=anchor.number,
            block_timestamp_s=anchor.timestamp_s,
            observed_at_ns=static_config.captured_at_ns,
        )
        round_payload = dict(source_round.payload)
        round_payload["historical_reconstruction"] = {
            "method": "public_logs_current_static_config_v1",
            "static_config_head_block": static_config.head_block,
            "decision_anchor_block": anchor.number,
            "decision_anchor_hash": anchor.block_hash.lower(),
            "bet_log_count": len(contributing),
            "bet_log_sha256": _bet_digest(contributing),
        }
        source_round = EventRecord(
            event_id=source_round.event_id,
            source=source_round.source,
            topic=source_round.topic,
            event_time_ns=source_round.event_time_ns,
            observed_at_ns=source_round.observed_at_ns,
            payload=round_payload,
        )
        source_oracle_reference = normalize_oracle_reference(
            static_config.oracle_address,
            contract_address=prediction_contract,
            block_number=anchor.number,
            block_timestamp_s=anchor.timestamp_s,
            observed_at_ns=static_config.captured_at_ns,
        )
        oracle_values = _oracle_round_at_or_before_cutoff(
            client,
            oracle_address=static_config.oracle_address,
            starting_round_id=_lock_oracle_round_id(timeline),
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

        policy = ReconstructionPolicy(
            dataset_id=dataset_id,
            assumed_latency_ns=assumed_onchain_latency_ns,
            captured_at_ns=static_config.captured_at_ns,
        )
        reconstructed_round = reconstruct_event(
            source_round,
            policy=policy,
            availability_base_ns=anchor.timestamp_s * 1_000_000_000,
            availability_basis="decision_anchor_block_timestamp",
        )
        reconstructed_oracle_reference = reconstruct_event(
            source_oracle_reference,
            policy=policy,
            availability_base_ns=anchor.timestamp_s * 1_000_000_000,
            availability_basis="decision_anchor_block_timestamp",
        )
        reconstructed_chainlink = reconstruct_event(
            source_chainlink,
            policy=policy,
            availability_base_ns=int(oracle_values[3]) * 1_000_000_000,
            availability_basis="chainlink_updated_at",
        )
        if reconstructed_round.observed_at_ns > cutoff_ns:
            raise AssertionError("round decision snapshot is unavailable by cutoff")
        if reconstructed_chainlink.observed_at_ns > cutoff_ns:
            raise AssertionError("Chainlink decision snapshot is unavailable by cutoff")

        if reconstructed_round.event_id in existing:
            already_present.append(timeline.epoch)
            continue
        batch = [reconstructed_round, reconstructed_oracle_reference]
        if reconstructed_chainlink.event_id not in existing:
            batch.append(reconstructed_chainlink)
        stored = store.append_many(batch)
        existing.update(item.event.event_id for item in stored)
        points.append(
            DecisionSnapshotPoint(
                epoch=timeline.epoch,
                decision_cutoff_ns=cutoff_ns,
                block_number=anchor.number,
                block_timestamp_s=anchor.timestamp_s,
                reconstructed_observed_at_ns=reconstructed_round.observed_at_ns,
            )
        )

    return DecisionSnapshotBackfillResult(
        points=tuple(points),
        already_present_epochs=tuple(already_present),
    )


def run_public_rpc_historical_evidence_acquisition(
    store: EventStore,
    *,
    config: HistoricalPipelineConfig,
    binance_client: Any,
    rpc_client: ReadOnlyJsonRpcClient,
    from_block: int,
    to_block: int,
    symbol: str = "BNBUSDT",
    log_chunk_size: int = 5_000,
    binance_batch_limit: int = 1_000,
    binance_max_batches_per_window: int = 10_000,
    max_oracle_backtrack_rounds: int = 512,
) -> PublicRpcHistoricalEvidenceRunResult:
    """Populate a reconstructed research store without archive RPC state reads."""

    if store.mode != "reconstructed":
        raise ValueError("public RPC historical acquisition requires reconstructed Event Store")
    config.validate()
    if from_block < 0 or to_block < from_block:
        raise ValueError("historical block range is invalid")
    if not 1 <= binance_batch_limit <= 1_000:
        raise ValueError("binance_batch_limit must be in [1, 1000]")
    if log_chunk_size <= 0 or binance_max_batches_per_window <= 0:
        raise ValueError("chunk/batch limits must be positive")
    if not store.verify_chain():
        raise ValueError("source Event Store hash chain verification failed before acquisition")

    static_config = read_current_prediction_static_config(rpc_client)
    if to_block > static_config.head_block:
        raise ValueError("historical to_block is above captured current head")
    config_changes = count_relevant_prediction_config_changes(
        rpc_client,
        from_block=from_block,
        to_block=static_config.head_block,
        chunk_size=log_chunk_size,
    )
    if config_changes:
        raise ValueError(
            "Prediction interval/treasury/oracle configuration changed after historical range start"
        )

    pipeline = HistoricalPipeline(store, config)
    lifecycle = pipeline.backfill_lifecycle(
        rpc_client,
        from_block=from_block,
        to_block=to_block,
        chunk_size=log_chunk_size,
    )
    timelines = build_round_timelines(
        (item.event for item in store.read_all_ingest_order()),
        interval_seconds=static_config.interval_seconds,
    )
    if not timelines.completed:
        raise ValueError("historical evidence range contains no completed Prediction rounds")

    bets = collect_prediction_bet_logs(
        rpc_client,
        from_block=from_block,
        to_block=to_block,
        chunk_size=log_chunk_size,
    )
    windows = build_historical_binance_windows(timelines.completed, config)
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

    decision_snapshots = backfill_public_rpc_decision_snapshots(
        rpc_client,
        store,
        timelines.completed,
        bets,
        dataset_id=config.dataset_id,
        static_config=static_config,
        decision_lead_ns=config.decision_lead_ns,
        assumed_onchain_latency_ns=config.assumed_onchain_latency_ns,
        max_oracle_backtrack_rounds=max_oracle_backtrack_rounds,
    )
    examples = pipeline.build_examples()
    events = store.read_all_ingest_order()
    if not events or not store.verify_chain():
        raise ValueError("reconstructed Event Store is empty or hash verification failed")

    return PublicRpcHistoricalEvidenceRunResult(
        dataset_id=config.dataset_id,
        static_config=static_config,
        config_change_logs=config_changes,
        completed_rounds=len(timelines.completed),
        incomplete_epochs=timelines.incomplete_epochs,
        bet_logs=len(bets),
        decision_snapshots=decision_snapshots,
        binance_windows=len(windows),
        binance_events=sum(item.events_appended for item in binance_results),
        examples=len(examples.examples),
        skipped_examples=len(examples.skipped),
        store_event_count=len(events),
        store_tip_hash=events[-1].event_hash,
    )
