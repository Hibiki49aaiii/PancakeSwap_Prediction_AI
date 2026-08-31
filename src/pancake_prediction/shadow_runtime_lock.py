from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .process_lock import SqliteExclusiveProcessLock, SqliteProcessLockError


class ShadowRuntimeLockError(RuntimeError):
    pass


def shadow_runtime_lock_path(shadow_database: Path) -> Path:
    resolved = shadow_database.expanduser().resolve(strict=False)
    return resolved.with_name(resolved.name + ".runtime-lock.sqlite3")


@dataclass(slots=True)
class ShadowRuntimeProcessLock:
    shadow_database: Path
    _lock: SqliteExclusiveProcessLock | None = field(
        init=False,
        default=None,
        repr=False,
    )

    @property
    def lock_database(self) -> Path:
        return shadow_runtime_lock_path(self.shadow_database)

    @property
    def acquired(self) -> bool:
        return self._lock is not None and self._lock.acquired

    def acquire(self) -> ShadowRuntimeProcessLock:
        if self._lock is not None:
            raise ShadowRuntimeLockError("Stage 4 runtime lock is already acquired")

        lock = SqliteExclusiveProcessLock(
            self.lock_database,
            contention_message=(
                "another Stage 4 runtime already holds this campaign lock"
            ),
            failure_message="Stage 4 runtime campaign lock could not be acquired",
        )
        try:
            lock.acquire()
        except SqliteProcessLockError as exc:
            raise ShadowRuntimeLockError(str(exc)) from None
        self._lock = lock
        return self

    def release(self) -> None:
        lock = self._lock
        self._lock = None
        if lock is not None:
            lock.release()

    def __enter__(self) -> ShadowRuntimeProcessLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.release()
