from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import CHAIN_ID_BSC, market
from .store import BlockObservation, EventStore, IngestReport, RawLogObservation


class CollectionError(RuntimeError):
    pass


class SnapshotChangedError(CollectionError):
    pass


class ReorgBeyondLookbackError(CollectionError):
    pass


class HistoricalRpc(Protocol):
    def chain_id(self) -> int: ...

    def block_number(self) -> int: ...

    def block(self, number: int) -> dict[str, Any]: ...

    def get_logs(self, *, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class CollectionReport:
    market: str
    start_block: int
    end_block: int
    chunks: int
    blocks_inserted: int
    logs_inserted: int
    canonical_assignments: int
    reorgs_observed: int


@dataclass(frozen=True, slots=True)
class IncrementalCollectionPlan:
    source_key: str
    start_block: int
    end_block: int
    head_block: int
    safe_depth: int
    reorg_lookback_blocks: int


def _hex_int(value: object, *, name: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise CollectionError(f"{name} must be 0x-prefixed hex")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise CollectionError(f"{name} is invalid hex") from exc


def _parse_block(chain_id: int, expected_number: int, raw: dict[str, Any]) -> BlockObservation:
    number = _hex_int(raw.get("number"), name="block.number")
    if number != expected_number:
        raise CollectionError(f"RPC returned block {number} for requested {expected_number}")
    block_hash = raw.get("hash")
    parent_hash = raw.get("parentHash")
    if not isinstance(block_hash, str) or not isinstance(parent_hash, str):
        raise CollectionError("block hash/parentHash missing")
    return BlockObservation(
        chain_id=chain_id,
        block_number=number,
        block_hash=block_hash,
        parent_hash=parent_hash,
        timestamp=_hex_int(raw.get("timestamp"), name="block.timestamp"),
    )


def _parse_log(chain_id: int, raw: dict[str, Any]) -> RawLogObservation:
    topics = raw.get("topics")
    if not isinstance(topics, list) or not all(isinstance(topic, str) for topic in topics):
        raise CollectionError("log topics missing or invalid")
    address = raw.get("address")
    block_hash = raw.get("blockHash")
    tx_hash = raw.get("transactionHash")
    data = raw.get("data")
    if not all(isinstance(value, str) for value in (address, block_hash, tx_hash, data)):
        raise CollectionError("log identity/data fields missing")
    return RawLogObservation(
        chain_id=chain_id,
        block_number=_hex_int(raw.get("blockNumber"), name="log.blockNumber"),
        block_hash=str(block_hash),
        tx_hash=str(tx_hash),
        tx_index=_hex_int(raw.get("transactionIndex"), name="log.transactionIndex"),
        log_index=_hex_int(raw.get("logIndex"), name="log.logIndex"),
        address=str(address),
        topics=tuple(str(topic) for topic in topics),
        data=str(data),
        removed=bool(raw.get("removed", False)),
    )


def _merge_reports(reports: list[IngestReport]) -> tuple[int, int, int, int]:
    return (
        sum(report.blocks_inserted for report in reports),
        sum(report.logs_inserted for report in reports),
        sum(report.canonical_assignments for report in reports),
        sum(report.reorgs_observed for report in reports),
    )


class HistoricalCollector:
    def __init__(self, rpc: HistoricalRpc, store: EventStore) -> None:
        self.rpc = rpc
        self.store = store

    def collect_range(
        self,
        market_symbol: str,
        *,
        start_block: int,
        end_block: int,
        chunk_size: int = 250,
        observed_at: int | None = None,
        source_key: str | None = None,
    ) -> CollectionReport:
        if start_block < 0 or end_block < start_block:
            raise ValueError("invalid block range")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        chain_id = self.rpc.chain_id()
        if chain_id != CHAIN_ID_BSC:
            raise CollectionError(f"expected BNB Chain id {CHAIN_ID_BSC}, got {chain_id}")
        config = market(market_symbol)
        observed = int(time.time()) if observed_at is None else observed_at
        reports: list[IngestReport] = []
        chunks = 0

        for chunk_start in range(start_block, end_block + 1, chunk_size):
            chunk_end = min(end_block, chunk_start + chunk_size - 1)
            headers = [
                _parse_block(chain_id, height, self.rpc.block(height))
                for height in range(chunk_start, chunk_end + 1)
            ]
            for previous, current in zip(headers, headers[1:], strict=False):
                if current.parent_hash.lower() != previous.block_hash.lower():
                    raise SnapshotChangedError(
                        f"non-contiguous chain between {previous.block_number} and {current.block_number}"
                    )

            previous_canonical = (
                self.store.canonical_hash(chain_id, chunk_start - 1) if chunk_start > 0 else None
            )
            if previous_canonical is not None and headers:
                if headers[0].parent_hash.lower() != previous_canonical.lower():
                    raise ReorgBeyondLookbackError(
                        f"block {chunk_start} no longer descends from stored canonical block "
                        f"{chunk_start - 1}; increase reorg lookback"
                    )

            raw_logs = self.rpc.get_logs(
                address=config.address,
                from_block=chunk_start,
                to_block=chunk_end,
            )
            parsed_logs = [_parse_log(chain_id, raw) for raw in raw_logs]
            header_by_height = {header.block_number: header for header in headers}
            for log in parsed_logs:
                header = header_by_height.get(log.block_number)
                if header is None:
                    raise SnapshotChangedError("RPC returned a log outside the requested block range")
                if log.block_hash.lower() != header.block_hash.lower():
                    raise SnapshotChangedError(
                        f"log/header block hash mismatch at height {log.block_number}"
                    )
                if log.address.lower() != config.address.lower():
                    raise CollectionError("RPC returned a log from an unexpected contract address")

            end_recheck = _parse_block(chain_id, chunk_end, self.rpc.block(chunk_end))
            if end_recheck.block_hash.lower() != headers[-1].block_hash.lower():
                raise SnapshotChangedError(
                    f"chain tip for chunk changed while collecting block {chunk_end}"
                )

            report = self.store.ingest_observation_batch(
                blocks=headers,
                logs=parsed_logs,
                observed_at=observed,
            )
            reports.append(report)
            chunks += 1
            if source_key is not None:
                self.store.set_checkpoint(
                    source_key=source_key,
                    chain_id=chain_id,
                    market=config.symbol,
                    last_block=chunk_end,
                    last_block_hash=headers[-1].block_hash,
                    updated_at=observed,
                )

        blocks_inserted, logs_inserted, assignments, reorgs = _merge_reports(reports)
        return CollectionReport(
            market=config.symbol,
            start_block=start_block,
            end_block=end_block,
            chunks=chunks,
            blocks_inserted=blocks_inserted,
            logs_inserted=logs_inserted,
            canonical_assignments=assignments,
            reorgs_observed=reorgs,
        )

    def plan_incremental(
        self,
        market_symbol: str,
        *,
        source_key: str,
        initial_start_block: int,
        safe_depth: int = 12,
        reorg_lookback_blocks: int = 64,
    ) -> IncrementalCollectionPlan | None:
        if initial_start_block < 0 or safe_depth < 0 or reorg_lookback_blocks < 1:
            raise ValueError("invalid incremental collection settings")
        chain_id = self.rpc.chain_id()
        if chain_id != CHAIN_ID_BSC:
            raise CollectionError(f"expected BNB Chain id {CHAIN_ID_BSC}, got {chain_id}")
        config = market(market_symbol)
        head = self.rpc.block_number()
        end_block = head - safe_depth
        if end_block < initial_start_block:
            return None
        checkpoint = self.store.checkpoint(source_key)
        if checkpoint is None:
            start_block = initial_start_block
        else:
            if checkpoint.chain_id != CHAIN_ID_BSC or checkpoint.market != config.symbol:
                raise CollectionError("checkpoint source does not match chain/market")
            start_block = max(
                initial_start_block,
                checkpoint.last_block - reorg_lookback_blocks + 1,
            )
        if start_block > end_block:
            return None
        return IncrementalCollectionPlan(
            source_key=source_key,
            start_block=start_block,
            end_block=end_block,
            head_block=head,
            safe_depth=safe_depth,
            reorg_lookback_blocks=reorg_lookback_blocks,
        )

    def collect_incremental(
        self,
        market_symbol: str,
        *,
        source_key: str,
        initial_start_block: int,
        safe_depth: int = 12,
        reorg_lookback_blocks: int = 64,
        chunk_size: int = 250,
        observed_at: int | None = None,
    ) -> CollectionReport | None:
        plan = self.plan_incremental(
            market_symbol,
            source_key=source_key,
            initial_start_block=initial_start_block,
            safe_depth=safe_depth,
            reorg_lookback_blocks=reorg_lookback_blocks,
        )
        if plan is None:
            return None
        return self.collect_range(
            market_symbol,
            start_block=plan.start_block,
            end_block=plan.end_block,
            chunk_size=chunk_size,
            observed_at=observed_at,
            source_key=source_key,
        )
