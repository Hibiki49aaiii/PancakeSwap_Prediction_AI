from __future__ import annotations

from pathlib import Path

import pytest

from pancake_prediction.shadow_runtime_lock import (
    ShadowRuntimeLockError,
    ShadowRuntimeProcessLock,
    shadow_runtime_lock_path,
)


def test_shadow_runtime_lock_path_is_deterministic_per_resolved_shadow_db(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "nested" / "shadow.sqlite3"
    equivalent = tmp_path / "nested" / ".." / "nested" / "shadow.sqlite3"
    other = tmp_path / "nested" / "other.sqlite3"

    assert shadow_runtime_lock_path(shadow) == shadow_runtime_lock_path(equivalent)
    assert shadow_runtime_lock_path(shadow) != shadow_runtime_lock_path(other)
    assert shadow_runtime_lock_path(shadow).name == (
        "shadow.sqlite3.runtime-lock.sqlite3"
    )


def test_shadow_runtime_lock_rejects_concurrent_second_acquisition(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow.sqlite3"
    first = ShadowRuntimeProcessLock(shadow)
    second = ShadowRuntimeProcessLock(shadow)

    first.acquire()
    try:
        assert first.acquired is True
        assert first.lock_database.is_file()
        with pytest.raises(ShadowRuntimeLockError, match="already holds"):
            second.acquire()
        assert second.acquired is False
    finally:
        first.release()


def test_shadow_runtime_lock_can_reacquire_after_release(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow.sqlite3"
    first = ShadowRuntimeProcessLock(shadow)
    second = ShadowRuntimeProcessLock(shadow)

    first.acquire()
    first.release()
    second.acquire()
    try:
        assert second.acquired is True
    finally:
        second.release()


def test_shadow_runtime_lock_context_releases_after_exception(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow.sqlite3"
    first = ShadowRuntimeProcessLock(shadow)

    with pytest.raises(RuntimeError, match="cycle failed"), first:
        assert first.acquired is True
        raise RuntimeError("cycle failed")

    assert first.acquired is False
    with ShadowRuntimeProcessLock(shadow) as second:
        assert second.acquired is True


def test_shadow_runtime_lock_rejects_double_acquire_on_same_object(
    tmp_path: Path,
) -> None:
    lock = ShadowRuntimeProcessLock(tmp_path / "shadow.sqlite3")
    lock.acquire()
    try:
        with pytest.raises(ShadowRuntimeLockError, match="already acquired"):
            lock.acquire()
    finally:
        lock.release()
