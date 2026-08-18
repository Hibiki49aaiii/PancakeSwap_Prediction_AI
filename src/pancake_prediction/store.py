from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


class DataIntegrityError(RuntimeError):
    """Raised when immutable evidence conflicts with previously stored evidence."""


@dataclass(frozen=True, slots=True)
class BlockObservation:
    chain_id: int
    block_number: int
    block_hash: str
    parent_hash: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class RawLogObservation:
    chain_id: int
    block_number: int
    block_hash: str
    tx_hash: str
    tx_index: int
    log_index: int
    address: str
    topics: tuple[str, ...]
    data: str
    removed: bool = False


@dataclass(frozen=True, slots=True)
class IngestReport:
    blocks_inserted: int
    logs_inserted: int
    canonical_assignments: int
    reorgs_observed: int


@dataclass(frozen=True, slots=True)
class Checkpoint:
    source_key: str
    chain_id: int
    market: str
    last_block: int
    last_block_hash: str
    updated_at: int


def _validate_hash(value: str, *, name: str) -> str:
    normalized = value.lower()
    if len(normalized) != 66 or not normalized.startswith("0x"):
        raise ValueError(f"{name} must be a 32-byte hex value")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be hex") from exc
    return normalized


def _validate_address(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        raise ValueError("address must be a 20-byte hex value")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError("address must be hex") from exc
    return normalized


def _validate_data(value: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized[2:]) % 2:
        raise ValueError("log data must be even-length 0x-prefixed hex")
    try:
        bytes.fromhex(normalized[2:])
    except ValueError as exc:
        raise ValueError("log data must be hex") from exc
    return normalized


class EventStore:
    """SQLite evidence store that never overwrites raw blocks or logs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    chain_id INTEGER NOT NULL,
                    block_hash TEXT NOT NULL,
                    block_number INTEGER NOT NULL CHECK(block_number >= 0),
                    parent_hash TEXT NOT NULL,
                    timestamp INTEGER NOT NULL CHECK(timestamp >= 0),
                    first_observed_at INTEGER NOT NULL CHECK(first_observed_at >= 0),
                    PRIMARY KEY(chain_id, block_hash),
                    UNIQUE(chain_id, block_number, block_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_blocks_height
                    ON blocks(chain_id, block_number);

                CREATE TABLE IF NOT EXISTS raw_logs (
                    chain_id INTEGER NOT NULL,
                    block_hash TEXT NOT NULL,
                    block_number INTEGER NOT NULL CHECK(block_number >= 0),
                    tx_hash TEXT NOT NULL,
                    tx_index INTEGER NOT NULL CHECK(tx_index >= 0),
                    log_index INTEGER NOT NULL CHECK(log_index >= 0),
                    address TEXT NOT NULL,
                    topic0 TEXT,
                    topic1 TEXT,
                    topic2 TEXT,
                    topic3 TEXT,
                    data TEXT NOT NULL,
                    removed INTEGER NOT NULL CHECK(removed IN (0, 1)),
                    first_observed_at INTEGER NOT NULL CHECK(first_observed_at >= 0),
                    PRIMARY KEY(chain_id, block_hash, tx_hash, log_index),
                    FOREIGN KEY(chain_id, block_hash)
                        REFERENCES blocks(chain_id, block_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_logs_height
                    ON raw_logs(chain_id, block_number, log_index);

                CREATE TABLE IF NOT EXISTS canonical_assignments (
                    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id INTEGER NOT NULL,
                    block_number INTEGER NOT NULL CHECK(block_number >= 0),
                    block_hash TEXT NOT NULL,
                    observed_at INTEGER NOT NULL CHECK(observed_at >= 0),
                    reason TEXT NOT NULL CHECK(reason IN ('first_seen', 'reorg')),
                    FOREIGN KEY(chain_id, block_hash)
                        REFERENCES blocks(chain_id, block_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_canonical_height_seq
                    ON canonical_assignments(chain_id, block_number, assignment_id);

                CREATE TABLE IF NOT EXISTS reorg_observations (
                    reorg_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id INTEGER NOT NULL,
                    block_number INTEGER NOT NULL CHECK(block_number >= 0),
                    old_block_hash TEXT NOT NULL,
                    new_block_hash TEXT NOT NULL,
                    observed_at INTEGER NOT NULL CHECK(observed_at >= 0),
                    CHECK(old_block_hash <> new_block_hash)
                );

                CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
                    source_key TEXT PRIMARY KEY,
                    chain_id INTEGER NOT NULL,
                    market TEXT NOT NULL,
                    last_block INTEGER NOT NULL CHECK(last_block >= 0),
                    last_block_hash TEXT NOT NULL,
                    updated_at INTEGER NOT NULL CHECK(updated_at >= 0)
                );
                """
            )

    @staticmethod
    def _normalize_block(block: BlockObservation) -> BlockObservation:
        if block.chain_id <= 0 or block.block_number < 0 or block.timestamp < 0:
            raise ValueError("invalid block metadata")
        return BlockObservation(
            chain_id=block.chain_id,
            block_number=block.block_number,
            block_hash=_validate_hash(block.block_hash, name="block_hash"),
            parent_hash=_validate_hash(block.parent_hash, name="parent_hash"),
            timestamp=block.timestamp,
        )

    @staticmethod
    def _normalize_log(log: RawLogObservation) -> RawLogObservation:
        if log.chain_id <= 0 or min(log.block_number, log.tx_index, log.log_index) < 0:
            raise ValueError("invalid log metadata")
        if len(log.topics) > 4:
            raise ValueError("EVM log may contain at most four topics")
        topics = tuple(_validate_hash(topic, name="topic") for topic in log.topics)
        return RawLogObservation(
            chain_id=log.chain_id,
            block_number=log.block_number,
            block_hash=_validate_hash(log.block_hash, name="block_hash"),
            tx_hash=_validate_hash(log.tx_hash, name="tx_hash"),
            tx_index=log.tx_index,
            log_index=log.log_index,
            address=_validate_address(log.address),
            topics=topics,
            data=_validate_data(log.data),
            removed=bool(log.removed),
        )

    def ingest_observation_batch(
        self,
        *,
        blocks: Iterable[BlockObservation],
        logs: Iterable[RawLogObservation],
        observed_at: int,
    ) -> IngestReport:
        if observed_at < 0:
            raise ValueError("observed_at must be non-negative")
        normalized_blocks = tuple(self._normalize_block(block) for block in blocks)
        normalized_logs = tuple(self._normalize_log(log) for log in logs)
        block_keys = {(block.chain_id, block.block_hash) for block in normalized_blocks}
        for log in normalized_logs:
            if (log.chain_id, log.block_hash) not in block_keys:
                raise DataIntegrityError("every log must reference a block in the same ingest batch")

        blocks_inserted = 0
        logs_inserted = 0
        assignments = 0
        reorgs = 0
        with self.session() as db:
            db.execute("BEGIN IMMEDIATE")
            for block in normalized_blocks:
                existing = db.execute(
                    """SELECT block_number, parent_hash, timestamp
                       FROM blocks WHERE chain_id = ? AND block_hash = ?""",
                    (block.chain_id, block.block_hash),
                ).fetchone()
                if existing is None:
                    db.execute(
                        """INSERT INTO blocks(
                               chain_id, block_hash, block_number, parent_hash,
                               timestamp, first_observed_at
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            block.chain_id,
                            block.block_hash,
                            block.block_number,
                            block.parent_hash,
                            block.timestamp,
                            observed_at,
                        ),
                    )
                    blocks_inserted += 1
                elif (
                    int(existing["block_number"]) != block.block_number
                    or str(existing["parent_hash"]) != block.parent_hash
                    or int(existing["timestamp"]) != block.timestamp
                ):
                    raise DataIntegrityError(
                        f"conflicting immutable block evidence for {block.block_hash}"
                    )

                latest = db.execute(
                    """SELECT block_hash FROM canonical_assignments
                       WHERE chain_id = ? AND block_number = ?
                       ORDER BY assignment_id DESC LIMIT 1""",
                    (block.chain_id, block.block_number),
                ).fetchone()
                old_hash = str(latest["block_hash"]) if latest is not None else None
                if old_hash != block.block_hash:
                    reason = "first_seen" if old_hash is None else "reorg"
                    db.execute(
                        """INSERT INTO canonical_assignments(
                               chain_id, block_number, block_hash, observed_at, reason
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            block.chain_id,
                            block.block_number,
                            block.block_hash,
                            observed_at,
                            reason,
                        ),
                    )
                    assignments += 1
                    if old_hash is not None:
                        db.execute(
                            """INSERT INTO reorg_observations(
                                   chain_id, block_number, old_block_hash,
                                   new_block_hash, observed_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (
                                block.chain_id,
                                block.block_number,
                                old_hash,
                                block.block_hash,
                                observed_at,
                            ),
                        )
                        reorgs += 1

            for log in normalized_logs:
                topics = list(log.topics) + [None] * (4 - len(log.topics))
                existing = db.execute(
                    """SELECT block_number, tx_index, address, topic0, topic1,
                              topic2, topic3, data, removed
                       FROM raw_logs
                       WHERE chain_id = ? AND block_hash = ?
                         AND tx_hash = ? AND log_index = ?""",
                    (log.chain_id, log.block_hash, log.tx_hash, log.log_index),
                ).fetchone()
                expected = (
                    log.block_number,
                    log.tx_index,
                    log.address,
                    topics[0],
                    topics[1],
                    topics[2],
                    topics[3],
                    log.data,
                    int(log.removed),
                )
                if existing is None:
                    db.execute(
                        """INSERT INTO raw_logs(
                               chain_id, block_hash, block_number, tx_hash, tx_index,
                               log_index, address, topic0, topic1, topic2, topic3,
                               data, removed, first_observed_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            log.chain_id,
                            log.block_hash,
                            log.block_number,
                            log.tx_hash,
                            log.tx_index,
                            log.log_index,
                            log.address,
                            topics[0],
                            topics[1],
                            topics[2],
                            topics[3],
                            log.data,
                            int(log.removed),
                            observed_at,
                        ),
                    )
                    logs_inserted += 1
                else:
                    actual = tuple(existing[column] for column in existing.keys())
                    if actual != expected:
                        raise DataIntegrityError(
                            f"conflicting immutable log evidence for {log.tx_hash}:{log.log_index}"
                        )

        return IngestReport(
            blocks_inserted=blocks_inserted,
            logs_inserted=logs_inserted,
            canonical_assignments=assignments,
            reorgs_observed=reorgs,
        )

    def canonical_hash(self, chain_id: int, block_number: int) -> str | None:
        with self.session() as db:
            row = db.execute(
                """SELECT block_hash FROM canonical_assignments
                   WHERE chain_id = ? AND block_number = ?
                   ORDER BY assignment_id DESC LIMIT 1""",
                (chain_id, block_number),
            ).fetchone()
        return None if row is None else str(row["block_hash"])

    def block_count(self) -> int:
        with self.session() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM blocks").fetchone()
        assert row is not None
        return int(row["n"])

    def log_count(self) -> int:
        with self.session() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM raw_logs").fetchone()
        assert row is not None
        return int(row["n"])

    def reorg_count(self) -> int:
        with self.session() as db:
            row = db.execute("SELECT COUNT(*) AS n FROM reorg_observations").fetchone()
        assert row is not None
        return int(row["n"])

    def set_checkpoint(
        self,
        *,
        source_key: str,
        chain_id: int,
        market: str,
        last_block: int,
        last_block_hash: str,
        updated_at: int,
    ) -> None:
        normalized_hash = _validate_hash(last_block_hash, name="last_block_hash")
        if not source_key or chain_id <= 0 or last_block < 0 or updated_at < 0:
            raise ValueError("invalid checkpoint")
        with self.session() as db:
            db.execute(
                """INSERT INTO ingestion_checkpoints(
                       source_key, chain_id, market, last_block, last_block_hash, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_key) DO UPDATE SET
                       chain_id = excluded.chain_id,
                       market = excluded.market,
                       last_block = excluded.last_block,
                       last_block_hash = excluded.last_block_hash,
                       updated_at = excluded.updated_at""",
                (source_key, chain_id, market, last_block, normalized_hash, updated_at),
            )

    def checkpoint(self, source_key: str) -> Checkpoint | None:
        with self.session() as db:
            row = db.execute(
                "SELECT * FROM ingestion_checkpoints WHERE source_key = ?", (source_key,)
            ).fetchone()
        if row is None:
            return None
        return Checkpoint(
            source_key=str(row["source_key"]),
            chain_id=int(row["chain_id"]),
            market=str(row["market"]),
            last_block=int(row["last_block"]),
            last_block_hash=str(row["last_block_hash"]),
            updated_at=int(row["updated_at"]),
        )
