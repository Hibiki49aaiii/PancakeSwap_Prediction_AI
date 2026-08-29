from __future__ import annotations

from pathlib import Path

import pytest

from pancake_prediction.binance_live_lock import (
    BinanceLiveLineageLockError,
    BinanceLiveLineageProcessLock,
    binance_live_lineage_digest,
    binance_live_lineage_identity,
    binance_live_lineage_lock_path,
)
from pancake_prediction.clickhouse import ClickHouseHttpClient


def _client(
    endpoint: str = "http://EXAMPLE.invalid:80/",
    *,
    database: str = "default",
    username: str | None = None,
    password: str | None = None,
) -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        endpoint,
        database=database,
        username=username,
        password=password,
    )


def test_binance_live_lineage_identity_normalizes_endpoint_and_excludes_credentials(
    tmp_path: Path,
) -> None:
    first = _client(username="alice", password="first-secret")
    second = _client(
        "http://example.invalid",
        username="bob",
        password="second-secret",
    )

    first_identity = binance_live_lineage_identity(
        first,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
    )
    second_identity = binance_live_lineage_identity(
        second,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
    )

    assert first_identity == second_identity
    assert first_identity["clickhouse_endpoint"] == "http://example.invalid"
    assert "username" not in first_identity
    assert "password" not in first_identity
    assert binance_live_lineage_digest(
        first,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
    ) == binance_live_lineage_digest(
        second,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
    )

    lock_path = binance_live_lineage_lock_path(
        first,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
        lock_root=tmp_path,
    )
    assert lock_path.parent == tmp_path
    assert lock_path.name.endswith(".sqlite3")
    assert "example" not in lock_path.name
    assert "secret" not in lock_path.name
    assert len(lock_path.stem) == 64


def test_binance_live_lineage_lock_identity_changes_with_lineage(
    tmp_path: Path,
) -> None:
    client = _client()
    baseline = binance_live_lineage_lock_path(
        client,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
        lock_root=tmp_path,
    )
    changed_paths = {
        binance_live_lineage_lock_path(
            _client(database="other"),
            market="BNBUSD",
            venue="spot",
            timestamp_unit="milliseconds",
            availability_lag_ms=250,
            lock_root=tmp_path,
        ),
        binance_live_lineage_lock_path(
            client,
            market="BNBUSD",
            venue="um_futures",
            timestamp_unit="milliseconds",
            availability_lag_ms=250,
            lock_root=tmp_path,
        ),
        binance_live_lineage_lock_path(
            client,
            market="BNBUSD",
            venue="spot",
            timestamp_unit="microseconds",
            availability_lag_ms=250,
            lock_root=tmp_path,
        ),
        binance_live_lineage_lock_path(
            client,
            market="BNBUSD",
            venue="spot",
            timestamp_unit="milliseconds",
            availability_lag_ms=251,
            lock_root=tmp_path,
        ),
    }
    assert baseline not in changed_paths
    assert len(changed_paths) == 4


def test_binance_live_lineage_lock_rejects_same_lineage_concurrently(
    tmp_path: Path,
) -> None:
    client = _client()
    first = BinanceLiveLineageProcessLock(
        client,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
        lock_root=tmp_path,
    )
    second = BinanceLiveLineageProcessLock(
        client,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
        lock_root=tmp_path,
    )

    first.acquire()
    try:
        with pytest.raises(BinanceLiveLineageLockError, match="already writes"):
            second.acquire()
        assert second.acquired is False
    finally:
        first.release()

    second.acquire()
    try:
        assert second.acquired is True
    finally:
        second.release()


def test_binance_live_lineage_locks_allow_different_venues(
    tmp_path: Path,
) -> None:
    client = _client()
    spot = BinanceLiveLineageProcessLock(
        client,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
        lock_root=tmp_path,
    )
    perp = BinanceLiveLineageProcessLock(
        client,
        market="BNBUSD",
        venue="um_futures",
        timestamp_unit="milliseconds",
        availability_lag_ms=250,
        lock_root=tmp_path,
    )

    with spot, perp:
        assert spot.acquired is True
        assert perp.acquired is True
        assert spot.lock_database != perp.lock_database
