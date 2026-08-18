from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .store import DataIntegrityError, EventStore


@dataclass(frozen=True, slots=True)
class SnapshotBlock:
    block_number: int
    block_hash: str
    parent_hash: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class CanonicalSnapshot:
    chain_id: int
    start_block: int
    end_block: int
    blocks: tuple[SnapshotBlock, ...]
    snapshot_hash: str

    def block_hashes(self) -> dict[int, str]:
        return {block.block_number: block.block_hash for block in self.blocks}


@dataclass(frozen=True, slots=True)
class SnapshotLog:
    block_number: int
    block_hash: str
    tx_hash: str
    tx_index: int
    log_index: int
    address: str
    topics: tuple[str, ...]
    data: str
    removed: bool


def _snapshot_digest(chain_id: int, blocks: tuple[SnapshotBlock, ...]) -> str:
    payload = {
        "chain_id": chain_id,
        "blocks": [
            {
                "block_number": block.block_number,
                "block_hash": block.block_hash,
                "parent_hash": block.parent_hash,
                "timestamp": block.timestamp,
            }
            for block in blocks
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()


def freeze_canonical_snapshot(
    store: EventStore,
    *,
    chain_id: int,
    start_block: int,
    end_block: int,
) -> CanonicalSnapshot:
    if chain_id <= 0 or start_block < 0 or end_block < start_block:
        raise ValueError("invalid snapshot range")
    with store.session() as db:
        rows = db.execute(
            """
            SELECT ca.block_number, ca.block_hash, b.parent_hash, b.timestamp
            FROM canonical_assignments AS ca
            JOIN blocks AS b
              ON b.chain_id = ca.chain_id AND b.block_hash = ca.block_hash
            WHERE ca.chain_id = ?
              AND ca.block_number BETWEEN ? AND ?
              AND ca.assignment_id = (
                  SELECT MAX(newer.assignment_id)
                  FROM canonical_assignments AS newer
                  WHERE newer.chain_id = ca.chain_id
                    AND newer.block_number = ca.block_number
              )
            ORDER BY ca.block_number ASC
            """,
            (chain_id, start_block, end_block),
        ).fetchall()
    expected_count = end_block - start_block + 1
    if len(rows) != expected_count:
        present = {int(row["block_number"]) for row in rows}
        missing = [height for height in range(start_block, end_block + 1) if height not in present]
        preview = ",".join(str(height) for height in missing[:8])
        raise DataIntegrityError(f"canonical snapshot has missing heights: {preview}")

    blocks = tuple(
        SnapshotBlock(
            block_number=int(row["block_number"]),
            block_hash=str(row["block_hash"]),
            parent_hash=str(row["parent_hash"]),
            timestamp=int(row["timestamp"]),
        )
        for row in rows
    )
    for expected_height, block in zip(range(start_block, end_block + 1), blocks, strict=True):
        if block.block_number != expected_height:
            raise DataIntegrityError("canonical snapshot heights are non-contiguous")
    for previous, current in zip(blocks, blocks[1:], strict=False):
        if current.parent_hash.lower() != previous.block_hash.lower():
            raise DataIntegrityError(
                f"canonical snapshot parent mismatch at block {current.block_number}"
            )

    return CanonicalSnapshot(
        chain_id=chain_id,
        start_block=start_block,
        end_block=end_block,
        blocks=blocks,
        snapshot_hash=_snapshot_digest(chain_id, blocks),
    )


def canonical_logs(
    store: EventStore,
    snapshot: CanonicalSnapshot,
    *,
    address: str | None = None,
) -> tuple[SnapshotLog, ...]:
    selected_hash = snapshot.block_hashes()
    normalized_address = address.lower() if address is not None else None
    with store.session() as db:
        rows = db.execute(
            """
            SELECT block_number, block_hash, tx_hash, tx_index, log_index, address,
                   topic0, topic1, topic2, topic3, data, removed
            FROM raw_logs
            WHERE chain_id = ? AND block_number BETWEEN ? AND ?
            ORDER BY block_number ASC, tx_index ASC, log_index ASC
            """,
            (snapshot.chain_id, snapshot.start_block, snapshot.end_block),
        ).fetchall()

    result: list[SnapshotLog] = []
    for row in rows:
        block_number = int(row["block_number"])
        block_hash = str(row["block_hash"])
        row_address = str(row["address"])
        if selected_hash.get(block_number) != block_hash:
            continue
        if normalized_address is not None and row_address.lower() != normalized_address:
            continue
        topics = tuple(
            str(row[name])
            for name in ("topic0", "topic1", "topic2", "topic3")
            if row[name] is not None
        )
        result.append(
            SnapshotLog(
                block_number=block_number,
                block_hash=block_hash,
                tx_hash=str(row["tx_hash"]),
                tx_index=int(row["tx_index"]),
                log_index=int(row["log_index"]),
                address=row_address,
                topics=topics,
                data=str(row["data"]),
                removed=bool(row["removed"]),
            )
        )
    return tuple(result)


def raw_event_export_hash(snapshot: CanonicalSnapshot, logs: tuple[SnapshotLog, ...]) -> str:
    ordered = tuple(sorted(logs, key=lambda log: (log.block_number, log.tx_index, log.log_index)))
    if ordered != logs:
        raise DataIntegrityError("canonical export logs are not in deterministic chain order")
    selected_hash = snapshot.block_hashes()
    for log in logs:
        if selected_hash.get(log.block_number) != log.block_hash:
            raise DataIntegrityError(
                f"log {log.tx_hash}:{log.log_index} is outside the frozen canonical snapshot"
            )
    payload = {
        "snapshot_hash": snapshot.snapshot_hash,
        "logs": [
            {
                "block_number": log.block_number,
                "block_hash": log.block_hash,
                "tx_hash": log.tx_hash,
                "tx_index": log.tx_index,
                "log_index": log.log_index,
                "address": log.address,
                "topics": list(log.topics),
                "data": log.data,
                "removed": log.removed,
            }
            for log in logs
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "0x" + hashlib.sha256(encoded).hexdigest()
