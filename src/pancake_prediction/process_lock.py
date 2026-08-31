from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


class SqliteProcessLockError(RuntimeError):
    pass


@dataclass(slots=True)
class SqliteExclusiveProcessLock:
    lock_database: Path
    contention_message: str
    failure_message: str
    _connection: sqlite3.Connection | None = field(
        init=False,
        default=None,
        repr=False,
    )

    @property
    def acquired(self) -> bool:
        return self._connection is not None

    def acquire(self) -> SqliteExclusiveProcessLock:
        if self._connection is not None:
            raise SqliteProcessLockError("SQLite process lock is already acquired")

        self.lock_database.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.lock_database,
                timeout=0.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout = 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS process_lock (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1)
                )
                """
            )
            connection.execute("BEGIN EXCLUSIVE")
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                raise SqliteProcessLockError(self.contention_message) from None
            raise SqliteProcessLockError(self.failure_message) from None

        self._connection = connection
        return self

    def release(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            if connection.in_transaction:
                connection.rollback()
        finally:
            connection.close()

    def __enter__(self) -> SqliteExclusiveProcessLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.release()
