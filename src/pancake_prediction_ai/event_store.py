from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    source: str
    topic: str
    event_time_ns: int
    observed_at_ns: int
    payload: Mapping[str, Any]

    def validate(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.source:
            raise ValueError("source is required")
        if not self.topic:
            raise ValueError("topic is required")
        if self.event_time_ns < 0 or self.observed_at_ns < 0:
            raise ValueError("timestamps must be non-negative")
        try:
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be canonical JSON serializable") from exc


@dataclass(frozen=True, slots=True)
class StoredEvent:
    ingest_seq: int
    event: EventRecord
    prev_hash: str
    event_hash: str


class EventStore:
    """Append-only SQLite event store with a verifiable hash chain.

    Replay order is observation order (`observed_at_ns`, then `ingest_seq`), not
    source/event timestamp order. This is essential for leakage-safe simulation.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                ingest_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                topic TEXT NOT NULL,
                event_time_ns INTEGER NOT NULL,
                observed_at_ns INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_observed ON events(observed_at_ns, ingest_seq)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    @staticmethod
    def _canonical_body(event: EventRecord) -> bytes:
        body = {
            "event_id": event.event_id,
            "source": event.source,
            "topic": event.topic,
            "event_time_ns": event.event_time_ns,
            "observed_at_ns": event.observed_at_ns,
            "payload": event.payload,
        }
        return json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()

    @staticmethod
    def _payload_json(event: EventRecord) -> str:
        return json.dumps(
            event.payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @staticmethod
    def _hash(prev_hash: str, canonical_body: bytes) -> str:
        return hashlib.sha256(prev_hash.encode() + b"|" + canonical_body).hexdigest()

    def append(self, event: EventRecord) -> StoredEvent:
        return self.append_many((event,))[0]

    def append_many(self, events: Iterable[EventRecord]) -> tuple[StoredEvent, ...]:
        """Append one logical event batch atomically while extending the hash chain.

        Every event is validated before the transaction starts. If any insert in
        the batch conflicts or fails, SQLite rolls the entire batch back; a
        consumer can therefore never observe a partial protocol snapshot.
        """

        batch = tuple(events)
        if not batch:
            return ()
        for event in batch:
            event.validate()
        event_ids = [event.event_id for event in batch]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("duplicate event_id inside append batch")

        stored: list[StoredEvent] = []
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT event_hash FROM events ORDER BY ingest_seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = str(row[0]) if row else "GENESIS"

            for event in batch:
                canonical = self._canonical_body(event)
                event_hash = self._hash(prev_hash, canonical)
                cur = self._conn.execute(
                    """
                    INSERT INTO events(
                        event_id, source, topic, event_time_ns, observed_at_ns,
                        payload_json, prev_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.source,
                        event.topic,
                        event.event_time_ns,
                        event.observed_at_ns,
                        self._payload_json(event),
                        prev_hash,
                        event_hash,
                    ),
                )
                stored.append(
                    StoredEvent(
                        ingest_seq=int(cur.lastrowid),
                        event=event,
                        prev_hash=prev_hash,
                        event_hash=event_hash,
                    )
                )
                prev_hash = event_hash
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise ValueError("duplicate or conflicting event in append batch") from exc
        except Exception:
            self._conn.rollback()
            raise
        return tuple(stored)

    @staticmethod
    def _decode_row(row: tuple[object, ...]) -> StoredEvent:
        ingest_seq, event_id, source, topic, event_time_ns, observed_at_ns, payload_json, prev_hash, event_hash = row
        event = EventRecord(
            event_id=str(event_id),
            source=str(source),
            topic=str(topic),
            event_time_ns=int(event_time_ns),
            observed_at_ns=int(observed_at_ns),
            payload=json.loads(str(payload_json)),
        )
        return StoredEvent(int(ingest_seq), event, str(prev_hash), str(event_hash))

    def read_as_of(self, cutoff_ns: int) -> tuple[StoredEvent, ...]:
        if cutoff_ns < 0:
            raise ValueError("cutoff_ns must be non-negative")
        rows = self._conn.execute(
            """
            SELECT ingest_seq, event_id, source, topic, event_time_ns,
                   observed_at_ns, payload_json, prev_hash, event_hash
            FROM events
            WHERE observed_at_ns <= ?
            ORDER BY observed_at_ns ASC, ingest_seq ASC
            """,
            (cutoff_ns,),
        ).fetchall()
        return tuple(self._decode_row(row) for row in rows)

    def read_all_ingest_order(self) -> tuple[StoredEvent, ...]:
        rows = self._conn.execute(
            """
            SELECT ingest_seq, event_id, source, topic, event_time_ns,
                   observed_at_ns, payload_json, prev_hash, event_hash
            FROM events ORDER BY ingest_seq ASC
            """
        ).fetchall()
        return tuple(self._decode_row(row) for row in rows)

    def verify_chain(self) -> bool:
        prev_hash = "GENESIS"
        for stored in self.read_all_ingest_order():
            if stored.prev_hash != prev_hash:
                return False
            expected = self._hash(prev_hash, self._canonical_body(stored.event))
            if stored.event_hash != expected:
                return False
            prev_hash = stored.event_hash
        return True
