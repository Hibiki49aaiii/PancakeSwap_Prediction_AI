from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .event_store import EventRecord, EventStore, StoredEvent
from .onchain_collector import PinnedProtocolSnapshot, collect_protocol_snapshot_at_block
from .pancake_contract import BNB_CHAIN_ID, BNB_PREDICTION_CONTRACT
from .read_only_rpc import ReadOnlyJsonRpcClient, RpcError


ClockNs = Callable[[], int]


@dataclass(frozen=True, slots=True)
class ObservedBlockHeader:
    number: int
    block_hash: str
    parent_hash: str
    timestamp_s: int

    def validate(self) -> None:
        if self.number < 0:
            raise ValueError("block number must be non-negative")
        if self.timestamp_s <= 0:
            raise ValueError("block timestamp must be positive")
        for name, value in (("block_hash", self.block_hash), ("parent_hash", self.parent_hash)):
            if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
                raise ValueError(f"{name} must be 32-byte hex")
            try:
                int(value[2:], 16)
            except ValueError as exc:
                raise ValueError(f"{name} must be hex") from exc


@dataclass(frozen=True, slots=True)
class ProtocolWatchResult:
    status: str
    header: ObservedBlockHeader
    protocol_snapshot: PinnedProtocolSnapshot | None
    stored_events: tuple[StoredEvent, ...]
    anomalies: tuple[str, ...]


def _hex_int(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RpcError(f"{field} must be hex string")
    return int(value, 16)


def _bytes32(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 66:
        raise RpcError(f"{field} must be 32-byte hex")
    try:
        int(value[2:], 16)
    except ValueError as exc:
        raise RpcError(f"{field} must be hex") from exc
    return value.lower()


def fetch_current_header(client: ReadOnlyJsonRpcClient) -> ObservedBlockHeader:
    if client.chain_id() != BNB_CHAIN_ID:
        raise ValueError(f"expected BNB chain id {BNB_CHAIN_ID}")
    number = client.block_number()
    raw = client.call("eth_getBlockByNumber", [hex(number), False])
    if not isinstance(raw, dict):
        raise RpcError("eth_getBlockByNumber must return an object")
    returned_number = _hex_int(raw.get("number"), "block.number")
    if returned_number != number:
        raise RpcError("current block number changed inside header response")
    header = ObservedBlockHeader(
        number=number,
        block_hash=_bytes32(raw.get("hash"), "block.hash"),
        parent_hash=_bytes32(raw.get("parentHash"), "block.parentHash"),
        timestamp_s=_hex_int(raw.get("timestamp"), "block.timestamp"),
    )
    header.validate()
    return header


def _anchor_event(
    header: ObservedBlockHeader,
    *,
    observed_at_ns: int,
) -> EventRecord:
    return EventRecord(
        event_id=f"collector:protocol:block_anchor:{header.number}:{header.block_hash}",
        source="collector",
        topic="collector.protocol_block_anchor",
        event_time_ns=header.timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "chain_id": BNB_CHAIN_ID,
            "block_number": header.number,
            "block_hash": header.block_hash,
            "parent_hash": header.parent_hash,
            "block_timestamp_s": header.timestamp_s,
        },
    )


def _anomaly_event(
    anomaly: str,
    header: ObservedBlockHeader,
    *,
    observed_at_ns: int,
    previous: ObservedBlockHeader | None,
) -> EventRecord:
    previous_number = None if previous is None else previous.number
    previous_hash = None if previous is None else previous.block_hash
    return EventRecord(
        event_id=(
            f"collector:protocol:anomaly:{anomaly}:{header.number}:"
            f"{header.block_hash}:{observed_at_ns}"
        ),
        source="collector",
        topic="collector.protocol_anomaly",
        event_time_ns=header.timestamp_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "anomaly": anomaly,
            "block_number": header.number,
            "block_hash": header.block_hash,
            "parent_hash": header.parent_hash,
            "previous_block_number": previous_number,
            "previous_block_hash": previous_hash,
        },
    )


def latest_observed_protocol_header(store: EventStore) -> ObservedBlockHeader | None:
    if store.mode != "observed":
        raise ValueError("protocol observation requires observed Event Store")
    for stored in reversed(store.read_all_ingest_order()):
        event = stored.event
        if event.source != "collector" or event.topic != "collector.protocol_block_anchor":
            continue
        payload = event.payload
        header = ObservedBlockHeader(
            number=int(payload["block_number"]),
            block_hash=str(payload["block_hash"]),
            parent_hash=str(payload["parent_hash"]),
            timestamp_s=int(payload["block_timestamp_s"]),
        )
        header.validate()
        return header
    return None


def observe_protocol_head_once(
    client: ReadOnlyJsonRpcClient,
    store: EventStore,
    *,
    prediction_contract: str = BNB_PREDICTION_CONTRACT,
    clock_ns: ClockNs = time.time_ns,
) -> ProtocolWatchResult:
    """Observe the current BSC head and atomically persist protocol state.

    Reorg/gap conditions are durable audit events. A same-height hash change is
    not allowed to overwrite the already-recorded round snapshot; instead the
    watcher records the reorg anomaly and waits for a new canonical height.
    """

    if store.mode != "observed":
        raise ValueError("protocol head watcher requires observed Event Store")
    header = fetch_current_header(client)
    previous = latest_observed_protocol_header(store)
    anomalies: list[str] = []

    if previous is not None:
        if header.number < previous.number:
            anomalies.append("chain_height_regression")
        elif header.number == previous.number:
            if header.block_hash == previous.block_hash:
                return ProtocolWatchResult(
                    status="unchanged",
                    header=header,
                    protocol_snapshot=None,
                    stored_events=(),
                    anomalies=(),
                )
            anomalies.append("same_height_reorg")
        else:
            if header.number > previous.number + 1:
                anomalies.append("observed_block_gap")
            elif header.parent_hash != previous.block_hash:
                anomalies.append("parent_hash_mismatch")

    observed_at_ns = clock_ns()
    if observed_at_ns < 0:
        raise ValueError("clock returned negative observation time")

    # Same/lower-height reorg conditions are audit-only. Round event IDs include
    # block number but not block hash, so persisting replacement state at that
    # height would collide and obscure what was actually observed first.
    if previous is not None and header.number <= previous.number:
        events = [_anchor_event(header, observed_at_ns=observed_at_ns)]
        events.extend(
            _anomaly_event(
                anomaly,
                header,
                observed_at_ns=observed_at_ns,
                previous=previous,
            )
            for anomaly in anomalies
        )
        stored = store.append_many(events)
        return ProtocolWatchResult(
            status="reorg_or_regression",
            header=header,
            protocol_snapshot=None,
            stored_events=stored,
            anomalies=tuple(anomalies),
        )

    snapshot = collect_protocol_snapshot_at_block(
        client,
        block_number=header.number,
        prediction_contract=prediction_contract,
        clock_ns=clock_ns,
    )
    if snapshot.anchor.block_hash.lower() != header.block_hash:
        raise RpcError("protocol snapshot block hash changed after head observation")

    event_observed_at_ns = snapshot.events[0].observed_at_ns
    events = [_anchor_event(header, observed_at_ns=event_observed_at_ns)]
    events.extend(
        _anomaly_event(
            anomaly,
            header,
            observed_at_ns=event_observed_at_ns,
            previous=previous,
        )
        for anomaly in anomalies
    )
    events.extend(snapshot.events)
    stored = store.append_many(events)
    return ProtocolWatchResult(
        status="observed",
        header=header,
        protocol_snapshot=snapshot,
        stored_events=stored,
        anomalies=tuple(anomalies),
    )
