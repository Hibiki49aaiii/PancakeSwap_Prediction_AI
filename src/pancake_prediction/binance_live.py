from __future__ import annotations

import hashlib
import http.client
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal, Protocol
from urllib.parse import urlencode

from .binance import PRICE_SCALE, QTY_SCALE, AggTrade, decimal_to_fixed
from .binance_archive import TimestampUnit
from .clickhouse import (
    ClickHouseInsertReport,
    ClickHouseJsonSink,
    ClickHouseParameterizedJsonSource,
    QueryParameter,
)
from .research_dataset import BINANCE_SYMBOL_BY_MARKET

LiveVenue = Literal["spot", "um_futures"]
_LIVE_VENUES: tuple[LiveVenue, ...] = ("spot", "um_futures")
_LIMIT = 1_000
_FUTURES_MAX_HISTORY_MS = 48 * 60 * 60 * 1_000
_MAX_TIME_WINDOW_MS = 60 * 60 * 1_000


class BinanceLiveError(RuntimeError):
    pass


class BinanceLiveSourceIntegrityError(BinanceLiveError):
    pass


@dataclass(frozen=True, slots=True)
class BinanceRestPage:
    rows: tuple[dict[str, object], ...]
    observed_at_ms: int
    source_sha256: str


class BinanceAggTradeSource(Protocol):
    def fetch_agg_trades(
        self,
        *,
        venue: LiveVenue,
        symbol: str,
        parameters: Mapping[str, int | str],
    ) -> BinanceRestPage: ...


@dataclass(slots=True)
class BinancePublicHttpClient:
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @staticmethod
    def _endpoint(venue: LiveVenue) -> tuple[str, str]:
        if venue == "spot":
            return "api.binance.com", "/api/v3/aggTrades"
        if venue == "um_futures":
            return "fapi.binance.com", "/fapi/v1/aggTrades"
        raise ValueError(f"unsupported live venue: {venue}")

    def fetch_agg_trades(
        self,
        *,
        venue: LiveVenue,
        symbol: str,
        parameters: Mapping[str, int | str],
    ) -> BinanceRestPage:
        hostname, path = self._endpoint(venue)
        normalized_symbol = symbol.upper()
        if not normalized_symbol.isalnum():
            raise ValueError("Binance symbol must be alphanumeric")
        query: dict[str, str] = {"symbol": normalized_symbol}
        for key, value in parameters.items():
            if key not in {"fromId", "startTime", "endTime", "limit"}:
                raise ValueError(f"unsupported Binance aggTrades parameter: {key}")
            query[key] = str(value)
        target = path + "?" + urlencode(query)

        connection = http.client.HTTPSConnection(
            hostname,
            443,
            timeout=self.timeout_seconds,
        )
        try:
            connection.request(
                "GET",
                target,
                headers={"Accept": "application/json"},
            )
            response = connection.getresponse()
            body = response.read()
            observed_at_ms = time.time_ns() // 1_000_000
            if response.status >= 400:
                detail = body[:1_000].decode(errors="replace").strip()
                suffix = f": {detail}" if detail else ""
                raise BinanceLiveError(
                    f"Binance {venue} HTTP {response.status}{suffix}"
                )
        except (OSError, http.client.HTTPException) as exc:
            raise BinanceLiveError(
                f"Binance {venue} request failed: {exc}"
            ) from exc
        finally:
            connection.close()

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise BinanceLiveError(
                f"Binance {venue} response was not valid JSON"
            ) from exc
        if not isinstance(payload, list):
            raise BinanceLiveError(
                f"Binance {venue} aggTrades response root must be an array"
            )

        rows: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise BinanceLiveError(
                    f"Binance {venue} aggTrades response contained a non-object row"
                )
            rows.append({str(key): value for key, value in item.items()})
        return BinanceRestPage(
            rows=tuple(rows),
            observed_at_ms=observed_at_ms,
            source_sha256=hashlib.sha256(body).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class BinanceLiveCursor:
    aggregate_trade_id: int
    trade_timestamp_ms: int
    source_name: str


@dataclass(frozen=True, slots=True)
class BinanceLiveCoverage:
    market: str
    venue: LiveVenue
    symbol: str
    timestamp_unit: TimestampUnit
    availability_lag_ms: int
    row_count: int
    first_available_at_ms: int | None
    last_available_at_ms: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BinanceLiveSyncReport:
    market: str
    venue: LiveVenue
    symbol: str
    availability_lag_ms: int
    timestamp_unit: TimestampUnit
    ingest_version: int
    pages: int
    batches: int
    rows: int
    bootstrap_start_timestamp_ms: int | None
    requested_end_timestamp_ms: int | None
    resumed_from_aggregate_trade_id: int | None
    first_aggregate_trade_id: int | None
    last_aggregate_trade_id: int | None
    first_trade_timestamp_ms: int | None
    last_trade_timestamp_ms: int | None
    first_available_at_ms: int | None
    last_available_at_ms: int | None
    response_chain_sha256: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _strict_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer") from exc
    return result


def _strict_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a JSON boolean")
    return value


def _parse_rest_trade(
    row: Mapping[str, object],
    *,
    symbol: str,
    observed_at_ms: int,
    availability_lag_ms: int,
) -> AggTrade:
    for field in ("a", "p", "q", "T", "m"):
        if field not in row:
            raise ValueError(f"Binance aggTrade row missing field {field}")
    trade_timestamp_ms = _strict_int(row["T"], field="T")
    aggregate_trade_id = _strict_int(row["a"], field="a")
    if trade_timestamp_ms < 0 or aggregate_trade_id < 0:
        raise ValueError("Binance aggTrade ids and timestamps must be non-negative")
    buyer_is_maker = _strict_bool(row["m"], field="m")
    available_at_ms = max(
        observed_at_ms,
        trade_timestamp_ms + availability_lag_ms,
    )
    return AggTrade(
        symbol=symbol.upper(),
        event_timestamp_ms=available_at_ms,
        trade_timestamp_ms=trade_timestamp_ms,
        price_e8=decimal_to_fixed(row["p"], scale=PRICE_SCALE),
        quantity_e8=decimal_to_fixed(row["q"], scale=QTY_SCALE),
        aggressive_side="sell" if buyer_is_maker else "buy",
        aggregate_trade_id=aggregate_trade_id,
    )


def inspect_binance_live_coverage(
    source: ClickHouseParameterizedJsonSource,
    *,
    market: str,
    venue: LiveVenue,
    availability_lag_ms: int,
    timestamp_unit: TimestampUnit = "milliseconds",
) -> BinanceLiveCoverage:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if venue not in _LIVE_VENUES:
        raise ValueError(f"unsupported live venue: {venue}")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    if timestamp_unit not in {"auto", "milliseconds", "microseconds"}:
        raise ValueError("unsupported timestamp_unit")

    symbol = BINANCE_SYMBOL_BY_MARKET[market]
    query = (
        "SELECT count() AS row_count, "
        "min(event_timestamp_ms) AS first_available_at_ms, "
        "max(event_timestamp_ms) AS last_available_at_ms "
        "FROM binance_agg_trades FINAL "
        "WHERE venue={venue:String} AND symbol={symbol:String} AND "
        "timestamp_unit={timestamp_unit:String} AND "
        "availability_lag_ms={availability_lag_ms:UInt32} AND "
        "source_name={source_name:String}"
    )
    parameters: dict[str, QueryParameter] = {
        "venue": venue,
        "symbol": symbol,
        "timestamp_unit": timestamp_unit,
        "availability_lag_ms": availability_lag_ms,
        "source_name": f"binance-rest:{venue}",
    }
    rows = tuple(source.query_json_rows(query, parameters=parameters))
    if len(rows) != 1:
        raise ValueError("Binance live coverage query must return exactly one row")
    row = rows[0]
    count = _strict_int(row.get("row_count"), field="row_count")
    if count < 0:
        raise ValueError("Binance live coverage row_count must be non-negative")
    if count == 0:
        return BinanceLiveCoverage(
            market=market,
            venue=venue,
            symbol=symbol,
            timestamp_unit=timestamp_unit,
            availability_lag_ms=availability_lag_ms,
            row_count=0,
            first_available_at_ms=None,
            last_available_at_ms=None,
        )
    first = _strict_int(
        row.get("first_available_at_ms"),
        field="first_available_at_ms",
    )
    last = _strict_int(
        row.get("last_available_at_ms"),
        field="last_available_at_ms",
    )
    if first < 0 or last < first:
        raise ValueError("Binance live coverage timestamps are inconsistent")
    return BinanceLiveCoverage(
        market=market,
        venue=venue,
        symbol=symbol,
        timestamp_unit=timestamp_unit,
        availability_lag_ms=availability_lag_ms,
        row_count=count,
        first_available_at_ms=first,
        last_available_at_ms=last,
    )


def latest_binance_live_cursor(
    source: ClickHouseParameterizedJsonSource,
    *,
    market: str,
    venue: LiveVenue,
    availability_lag_ms: int,
    timestamp_unit: TimestampUnit = "milliseconds",
) -> BinanceLiveCursor | None:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if venue not in _LIVE_VENUES:
        raise ValueError(f"unsupported live venue: {venue}")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    if timestamp_unit not in {"auto", "milliseconds", "microseconds"}:
        raise ValueError("unsupported timestamp_unit")
    query = (
        "SELECT aggregate_trade_id,trade_timestamp_ms,source_name "
        "FROM binance_agg_trades FINAL "
        "WHERE venue={venue:String} AND symbol={symbol:String} AND "
        "timestamp_unit={timestamp_unit:String} AND "
        "availability_lag_ms={availability_lag_ms:UInt32} "
        "ORDER BY aggregate_trade_id DESC LIMIT 1"
    )
    parameters: dict[str, QueryParameter] = {
        "venue": venue,
        "symbol": BINANCE_SYMBOL_BY_MARKET[market],
        "timestamp_unit": timestamp_unit,
        "availability_lag_ms": availability_lag_ms,
    }
    rows = tuple(source.query_json_rows(query, parameters=parameters))
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("latest Binance cursor query returned multiple rows")
    row = rows[0]
    trade_id = _strict_int(
        row.get("aggregate_trade_id"),
        field="aggregate_trade_id",
    )
    timestamp_ms = _strict_int(
        row.get("trade_timestamp_ms"),
        field="trade_timestamp_ms",
    )
    if trade_id < 0 or timestamp_ms < 0:
        raise ValueError("latest Binance cursor values must be non-negative")
    source_name = row.get("source_name")
    if not isinstance(source_name, str) or not source_name:
        raise ValueError("latest Binance cursor source_name must be non-empty")
    return BinanceLiveCursor(
        aggregate_trade_id=trade_id,
        trade_timestamp_ms=timestamp_ms,
        source_name=source_name,
    )


def _row_for_clickhouse(
    trade: AggTrade,
    *,
    venue: LiveVenue,
    availability_lag_ms: int,
    timestamp_unit: TimestampUnit,
    source_sha256: str,
    ingest_version: int,
) -> dict[str, object]:
    return {
        "venue": venue,
        "symbol": trade.symbol,
        "timestamp_unit": timestamp_unit,
        "event_timestamp_ms": trade.event_timestamp_ms,
        "trade_timestamp_ms": trade.trade_timestamp_ms,
        "aggregate_trade_id": trade.aggregate_trade_id,
        "price_e8": trade.price_e8,
        "quantity_e8": trade.quantity_e8,
        "aggressive_side": trade.aggressive_side,
        "source_sha256": source_sha256,
        "source_name": f"binance-rest:{venue}",
        "availability_lag_ms": availability_lag_ms,
        "ingest_version": ingest_version,
    }


def sync_binance_live_aggtrades(
    sink: ClickHouseJsonSink,
    cursor_source: ClickHouseParameterizedJsonSource,
    rest_source: BinanceAggTradeSource,
    *,
    market: str,
    venue: LiveVenue,
    availability_lag_ms: int,
    timestamp_unit: TimestampUnit = "milliseconds",
    now_timestamp_ms: int | None = None,
    bootstrap_window_ms: int = 120_000,
    batch_size: int = 5_000,
    max_pages: int = 100,
    ingest_version: int | None = None,
) -> BinanceLiveSyncReport:
    if market not in BINANCE_SYMBOL_BY_MARKET:
        raise ValueError(f"unsupported research market: {market}")
    if venue not in _LIVE_VENUES:
        raise ValueError(f"unsupported live venue: {venue}")
    if availability_lag_ms < 0:
        raise ValueError("availability_lag_ms must be non-negative")
    if timestamp_unit not in {"auto", "milliseconds", "microseconds"}:
        raise ValueError("unsupported timestamp_unit")
    if bootstrap_window_ms <= 0 or bootstrap_window_ms >= _MAX_TIME_WINDOW_MS:
        raise ValueError("bootstrap_window_ms must be positive and less than one hour")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    now_ms = (
        time.time_ns() // 1_000_000
        if now_timestamp_ms is None
        else now_timestamp_ms
    )
    if now_ms < 0:
        raise ValueError("now_timestamp_ms must be non-negative")
    version = time.time_ns() if ingest_version is None else ingest_version
    if version < 0 or version > (1 << 64) - 1:
        raise ValueError("ingest_version must fit UInt64")

    symbol = BINANCE_SYMBOL_BY_MARKET[market]
    prior_live_coverage = inspect_binance_live_coverage(
        cursor_source,
        market=market,
        venue=venue,
        availability_lag_ms=availability_lag_ms,
        timestamp_unit=timestamp_unit,
    )
    cursor = latest_binance_live_cursor(
        cursor_source,
        market=market,
        venue=venue,
        availability_lag_ms=availability_lag_ms,
        timestamp_unit=timestamp_unit,
    )
    expected_live_source = f"binance-rest:{venue}"
    if (
        prior_live_coverage.row_count > 0
        and cursor is not None
        and cursor.source_name != expected_live_source
    ):
        raise BinanceLiveSourceIntegrityError(
            "Binance lineage advanced by a non-live source after prospective "
            "observation began; start a new source-bound campaign"
        )
    cursor_is_recent = (
        cursor is not None
        and (
            venue != "um_futures"
            or cursor.trade_timestamp_ms >= now_ms - _FUTURES_MAX_HISTORY_MS
        )
    )

    bootstrap_start: int | None = None
    requested_end: int | None = None
    resumed_from: int | None = None
    if cursor_is_recent and cursor is not None:
        next_from_id = cursor.aggregate_trade_id + 1
        initial_parameters: dict[str, int | str] = {
            "fromId": next_from_id,
            "limit": _LIMIT,
        }
        resumed_from = next_from_id
    else:
        bootstrap_start = max(0, now_ms - bootstrap_window_ms)
        requested_end = now_ms
        initial_parameters = {
            "startTime": bootstrap_start,
            "endTime": requested_end,
            "limit": _LIMIT,
        }

    total_batches = 0
    total_rows = 0
    pages = 0
    first_id: int | None = None
    last_id: int | None = None
    first_trade_ms: int | None = None
    last_trade_ms: int | None = None
    first_available_ms: int | None = None
    last_available_ms: int | None = None
    response_digests: list[str] = []
    previous_id: int | None = None
    parameters = initial_parameters
    complete = False

    for page_index in range(max_pages):
        page = rest_source.fetch_agg_trades(
            venue=venue,
            symbol=symbol,
            parameters=parameters,
        )
        pages += 1
        response_digests.append(page.source_sha256)
        if page.observed_at_ms < 0:
            raise ValueError("Binance response observed_at_ms must be non-negative")
        if len(page.rows) > _LIMIT:
            raise ValueError("Binance aggTrades response exceeded the requested limit")
        if not page.rows:
            complete = True
            break

        trades: list[AggTrade] = []
        reached_requested_end = False
        for raw in page.rows:
            trade = _parse_rest_trade(
                raw,
                symbol=symbol,
                observed_at_ms=page.observed_at_ms,
                availability_lag_ms=availability_lag_ms,
            )
            if previous_id is not None and trade.aggregate_trade_id <= previous_id:
                raise ValueError(
                    "Binance live aggregate trade ids must be strictly increasing"
                )
            previous_id = trade.aggregate_trade_id
            if bootstrap_start is not None and trade.trade_timestamp_ms < bootstrap_start:
                continue
            if requested_end is not None and trade.trade_timestamp_ms > requested_end:
                reached_requested_end = True
                continue
            trades.append(trade)

        if trades:
            if first_id is None:
                first_id = trades[0].aggregate_trade_id
                first_trade_ms = trades[0].trade_timestamp_ms
                first_available_ms = trades[0].event_timestamp_ms
            last_id = trades[-1].aggregate_trade_id
            last_trade_ms = trades[-1].trade_timestamp_ms
            last_available_ms = trades[-1].event_timestamp_ms
            insert_report: ClickHouseInsertReport = sink.insert_json_rows(
                "binance_agg_trades",
                (
                    _row_for_clickhouse(
                        trade,
                        venue=venue,
                        availability_lag_ms=availability_lag_ms,
                        timestamp_unit=timestamp_unit,
                        source_sha256=page.source_sha256,
                        ingest_version=version,
                    )
                    for trade in trades
                ),
                batch_size=batch_size,
            )
            total_batches += insert_report.batches
            total_rows += insert_report.rows

        if reached_requested_end or len(page.rows) < _LIMIT:
            complete = True
            break

        raw_last = page.rows[-1]
        last_page_id = _strict_int(raw_last.get("a"), field="a")
        parameters = {
            "fromId": last_page_id + 1,
            "limit": _LIMIT,
        }
        if page_index + 1 == max_pages:
            break

    if not complete:
        raise BinanceLiveError(
            "Binance live sync reached max_pages before catching up; "
            "increase max_pages or poll more frequently"
        )

    chain = hashlib.sha256(
        ("\n".join(response_digests) + "\n").encode()
    ).hexdigest()
    return BinanceLiveSyncReport(
        market=market,
        venue=venue,
        symbol=symbol,
        availability_lag_ms=availability_lag_ms,
        timestamp_unit=timestamp_unit,
        ingest_version=version,
        pages=pages,
        batches=total_batches,
        rows=total_rows,
        bootstrap_start_timestamp_ms=bootstrap_start,
        requested_end_timestamp_ms=requested_end,
        resumed_from_aggregate_trade_id=resumed_from,
        first_aggregate_trade_id=first_id,
        last_aggregate_trade_id=last_id,
        first_trade_timestamp_ms=first_trade_ms,
        last_trade_timestamp_ms=last_trade_ms,
        first_available_at_ms=first_available_ms,
        last_available_at_ms=last_available_ms,
        response_chain_sha256=chain,
    )
