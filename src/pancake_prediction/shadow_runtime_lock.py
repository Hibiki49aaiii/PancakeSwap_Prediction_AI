from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


class ShadowRuntimeLockError(RuntimeError):
    pass


def shadow_runtime_lock_path(shadow_database: Path) -> Path:
    resolved = shadow_database.expanduser().resolve(strict=False)
    return resolved.with_name(resolved.name + ".runtime-lock.sqlite3")


@dataclass(slots=True)
class ShadowRuntimeProcessLock:
    shadow_database: Path
    _connection: sqlite3.Connection | None = field(
        init=False,
        default=None,
        repr=False,
    )

    @property
    def lock_database(self) -> Path:
        return shadow_runtime_lock_path(self.shadow_database)

    @property
    def acquired(self) -> bool:
        return self._connection is not None

    def acquire(self) -> ShadowRuntimeProcessLock:
        if self._connection is not None:
            raise ShadowRuntimeLockError("Stage 4 runtime lock is already acquired")

        path = self.lock_database
        path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                path,
                timeout=0.0,
                isolation_level=None,
            )
            connection.execute("PRAGMA busy_timeout = 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stage4_runtime_lock (
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
                raise ShadowRuntimeLockError(
                    "another Stage 4 runtime already holds this campaign lock"
                ) from None
            raise ShadowRuntimeLockError(
                "Stage 4 runtime campaign lock could not be acquired"
            ) from None

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

    def __enter__(self) -> ShadowRuntimeProcessLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.release()
