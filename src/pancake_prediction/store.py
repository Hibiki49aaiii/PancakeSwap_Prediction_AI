from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS blocks (
  chain_id INTEGER NOT NULL,
  number INTEGER NOT NULL,
  hash TEXT NOT NULL,
  parent_hash TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  canonical INTEGER NOT NULL CHECK (canonical IN (0,1)),
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (chain_id, number, hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_blocks_canonical_height
  ON blocks(chain_id, number) WHERE canonical=1;
CREATE INDEX IF NOT EXISTS idx_blocks_canonical_number
  ON blocks(chain_id, canonical, number);

CREATE TABLE IF NOT EXISTS events (
  chain_id INTEGER NOT NULL,
  contract_address TEXT NOT NULL,
  market TEXT,
  source TEXT NOT NULL,
  block_number INTEGER NOT NULL,
  block_hash TEXT NOT NULL,
  tx_hash TEXT NOT NULL,
  tx_index INTEGER NOT NULL,
  log_index INTEGER NOT NULL,
  topic0 TEXT NOT NULL,
  event_name TEXT,
  decoded_json TEXT,
  raw_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (chain_id, tx_hash, log_index, block_hash),
  FOREIGN KEY (chain_id, block_number, block_hash)
    REFERENCES blocks(chain_id, number, hash)
);
CREATE INDEX IF NOT EXISTS idx_events_market_block ON events(market, block_number);
CREATE INDEX IF NOT EXISTS idx_events_name_block ON events(event_name, block_number);

CREATE TABLE IF NOT EXISTS reorgs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_id INTEGER NOT NULL,
  block_number INTEGER NOT NULL,
  old_hash TEXT NOT NULL,
  new_hash TEXT NOT NULL,
  detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collector_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chain_id INTEGER NOT NULL,
  market TEXT NOT NULL,
  contract_address TEXT NOT NULL,
  from_block INTEGER NOT NULL,
  to_block INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running','success','failed')),
  details_json TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _hex_int(value: object) -> int:
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _normalized_hex(value: object) -> str:
    return str(value).lower()


@dataclass(slots=True)
class EventStore:
    path: Path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def upsert_block(self, chain_id: int, block: dict[str, Any]) -> bool:
        number = _hex_int(block["number"])
        block_hash = _normalized_hex(block["hash"])
        parent_hash = _normalized_hex(block["parentHash"])
        timestamp = _hex_int(block["timestamp"])
        reorged = False
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT hash FROM blocks WHERE chain_id=? AND number=? AND canonical=1",
                (chain_id, number),
            ).fetchone()
            if current is not None and str(current["hash"]).lower() != block_hash:
                old_hash = str(current["hash"]).lower()
                conn.execute(
                    "UPDATE blocks SET canonical=0 WHERE chain_id=? AND number=? AND canonical=1",
                    (chain_id, number),
                )
                conn.execute(
                    "INSERT INTO reorgs(chain_id,block_number,old_hash,new_hash) VALUES(?,?,?,?)",
                    (chain_id, number, old_hash, block_hash),
                )
                reorged = True
            conn.execute(
                """
                INSERT INTO blocks(chain_id,number,hash,parent_hash,timestamp,canonical)
                VALUES(?,?,?,?,?,1)
                ON CONFLICT(chain_id,number,hash) DO UPDATE SET
                  parent_hash=excluded.parent_hash,
                  timestamp=excluded.timestamp,
                  canonical=1
                """,
                (chain_id, number, block_hash, parent_hash, timestamp),
            )
            conn.commit()
        return reorged

    def canonical_blocks_from(self, chain_id: int, lower_block: int) -> list[sqlite3.Row]:
        with closing(self.connect()) as conn:
            return conn.execute(
                """
                SELECT * FROM blocks
                WHERE chain_id=? AND canonical=1 AND number>=?
                ORDER BY number
                """,
                (chain_id, lower_block),
            ).fetchall()

    def insert_event(
        self,
        *,
        chain_id: int,
        contract_address: str,
        market: str | None,
        source: str,
        log: dict[str, Any],
        event_name: str | None,
        decoded: dict[str, object] | None,
    ) -> bool:
        topics = log.get("topics")
        if not isinstance(topics, list) or not topics:
            raise ValueError("event log must include topic0")
        block_number = _hex_int(log["blockNumber"])
        block_hash = _normalized_hex(log["blockHash"])
        tx_hash = _normalized_hex(log["transactionHash"])
        tx_index = _hex_int(log.get("transactionIndex", 0))
        log_index = _hex_int(log["logIndex"])
        topic0 = _normalized_hex(topics[0])
        decoded_json = None
        if decoded is not None:
            decoded_json = json.dumps(decoded, sort_keys=True, separators=(",", ":"))
        raw_json = json.dumps(log, sort_keys=True, separators=(",", ":"))
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO events(
                  chain_id,contract_address,market,source,block_number,block_hash,
                  tx_hash,tx_index,log_index,topic0,event_name,decoded_json,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chain_id,
                    contract_address.lower(),
                    market,
                    source,
                    block_number,
                    block_hash,
                    tx_hash,
                    tx_index,
                    log_index,
                    topic0,
                    event_name,
                    decoded_json,
                    raw_json,
                ),
            )
            conn.commit()
            return cursor.rowcount == 1

    def record_metadata(self, key: str, value: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                """
                INSERT INTO metadata(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            conn.commit()

    def metadata(self, key: str) -> str | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def begin_collector_run(
        self,
        *,
        chain_id: int,
        market: str,
        contract_address: str,
        from_block: int,
        to_block: int,
        details: dict[str, object],
    ) -> int:
        payload = json.dumps(details, sort_keys=True, separators=(",", ":"))
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO collector_runs(
                  chain_id,market,contract_address,from_block,to_block,status,details_json
                ) VALUES(?,?,?,?,?,'running',?)
                """,
                (chain_id, market, contract_address.lower(), from_block, to_block, payload),
            )
            conn.commit()
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a collector run id")
            return int(cursor.lastrowid)

    def finish_collector_run(
        self, run_id: int, *, status: str, details: dict[str, object]
    ) -> None:
        if status not in {"success", "failed"}:
            raise ValueError("collector run status must be success or failed")
        payload = json.dumps(details, sort_keys=True, separators=(",", ":"))
        with closing(self.connect()) as conn:
            cursor = conn.execute(
                """
                UPDATE collector_runs
                SET status=?,details_json=?,finished_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='running'
                """,
                (status, payload, run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("collector run not found or already finished")
            conn.commit()

    def collector_run(self, run_id: int) -> sqlite3.Row | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM collector_runs WHERE id=?", (run_id,)).fetchone()
        return cast(sqlite3.Row | None, row)
