from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .binance_archive import TimestampUnit
from .binance_live import LiveVenue
from .clickhouse import ClickHouseHttpClient
from .process_lock import SqliteExclusiveProcessLock, SqliteProcessLockError
from .research_dataset import BINANCE_SYMBOL_BY_MARKET


class BinanceLiveLineageLockError(RuntimeError):
    pass


def _normalized_clickhouse_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("ClickHouse endpoint must be an http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ClickHouse credentials must not be embedded in endpoint")
    if parsed.query or parsed.fragment:
        raise ValueError("ClickHouse endpoint must not contain query or fragment")

    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    host_for_netloc = f"[{host}]" if ":" in host else host
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        host_for_netloc = f"{host_for_netloc}:{port}"

    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, host_for_netloc, path, "", ""))


def binance_live_lineage_identity(
    client: ClickHouseHttpClient,
    *,
    market: str,
    venue: LiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
) -> dict[str, object]:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if venue not in {"spot", "um_futures"}:
        raise ValueError(f"unsupported live venue: {venue}")
    if timestamp_unit not in {"auto", "milliseconds", "microseconds"}:
        raise ValueError("unsupported timestamp_unit")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    return {
        "clickhouse_endpoint": _normalized_clickhouse_endpoint(client.endpoint),
        "clickhouse_database": client.database,
        "market": market,
        "symbol": BINANCE_SYMBOL_BY_MARKET[market],
        "venue": venue,
        "timestamp_unit": timestamp_unit,
        "availability_lag_ms": availability_lag_ms,
    }


def binance_live_lineage_digest(
    client: ClickHouseHttpClient,
    *,
    market: str,
    venue: LiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
) -> str:
    identity = binance_live_lineage_identity(
        client,
        market=market,
        venue=venue,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
    )
    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256((canonical + "\n").encode()).hexdigest()


def binance_live_lineage_lock_path(
    client: ClickHouseHttpClient,
    *,
    market: str,
    venue: LiveVenue,
    timestamp_unit: TimestampUnit,
    availability_lag_ms: int,
    lock_root: Path | None = None,
) -> Path:
    digest = binance_live_lineage_digest(
        client,
        market=market,
        venue=venue,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
    )
    root = (
        Path(tempfile.gettempdir()) / "pancake-prediction-ai" / "binance-live-locks"
        if lock_root is None
        else lock_root.expanduser().resolve(strict=False)
    )
    return root / f"{digest}.sqlite3"


@dataclass(slots=True)
class BinanceLiveLineageProcessLock:
    client: ClickHouseHttpClient
    market: str
    venue: LiveVenue
    timestamp_unit: TimestampUnit
    availability_lag_ms: int
    lock_root: Path | None = None
    _lock: SqliteExclusiveProcessLock | None = field(
        init=False,
        default=None,
        repr=False,
    )

    @property
    def lock_database(self) -> Path:
        return binance_live_lineage_lock_path(
            self.client,
            market=self.market,
            venue=self.venue,
            timestamp_unit=self.timestamp_unit,
            availability_lag_ms=self.availability_lag_ms,
            lock_root=self.lock_root,
        )

    @property
    def acquired(self) -> bool:
        return self._lock is not None and self._lock.acquired

    def acquire(self) -> BinanceLiveLineageProcessLock:
        if self._lock is not None:
            raise BinanceLiveLineageLockError(
                "Binance live lineage lock is already acquired"
            )
        lock = SqliteExclusiveProcessLock(
            self.lock_database,
            contention_message=(
                "another process already writes this Binance live lineage"
            ),
            failure_message="Binance live lineage lock could not be acquired",
        )
        try:
            lock.acquire()
        except SqliteProcessLockError as exc:
            raise BinanceLiveLineageLockError(str(exc)) from None
        self._lock = lock
        return self

    def release(self) -> None:
        lock = self._lock
        self._lock = None
        if lock is not None:
            lock.release()

    def __enter__(self) -> BinanceLiveLineageProcessLock:
        return self.acquire()

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.release()
