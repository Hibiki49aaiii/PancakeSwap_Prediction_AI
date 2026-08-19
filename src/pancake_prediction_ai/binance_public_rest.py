from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .event_store import EventRecord
from .sources.binance import normalize_rest_agg_trade, normalize_rest_book_ticker


PUBLIC_MARKET_DATA_BASE_URL = "https://data-api.binance.vision"
_ALLOWED_PATHS = frozenset(
    {
        "/api/v3/time",
        "/api/v3/ticker/bookTicker",
        "/api/v3/aggTrades",
    }
)


class BinancePublicDataError(RuntimeError):
    pass


HttpGet = Callable[[str, float], bytes]
ClockNs = Callable[[], int]


def _default_http_get(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured public endpoint
        return response.read()


@dataclass(slots=True)
class BinancePublicRestClient:
    base_url: str = PUBLIC_MARKET_DATA_BASE_URL
    timeout_seconds: float = 10.0
    http_get: HttpGet = _default_http_get
    clock_ns: ClockNs = time.time_ns

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Binance base_url must be an http(s) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = self.base_url.rstrip("/")

    @staticmethod
    def _symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol or not symbol.isascii():
            raise ValueError("symbol must be non-empty ASCII")
        return symbol.upper()

    def _get_json(self, path: str, params: Mapping[str, object]) -> Any:
        if path not in _ALLOWED_PATHS:
            raise PermissionError(f"path outside Binance public-data boundary: {path}")
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        raw = self.http_get(url, self.timeout_seconds)
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BinancePublicDataError("Binance response is not valid JSON") from exc
        if isinstance(result, dict) and "code" in result and "msg" in result:
            raise BinancePublicDataError(f"Binance error {result['code']}: {result['msg']}")
        return result

    def server_time_ms(self) -> int:
        result = self._get_json("/api/v3/time", {})
        if not isinstance(result, dict) or not isinstance(result.get("serverTime"), int):
            raise BinancePublicDataError("Binance time response is invalid")
        return int(result["serverTime"])

    def book_ticker(self, symbol: str) -> Mapping[str, Any]:
        normalized = self._symbol(symbol)
        result = self._get_json(
            "/api/v3/ticker/bookTicker",
            {"symbol": normalized, "symbolStatus": "TRADING"},
        )
        if not isinstance(result, dict):
            raise BinancePublicDataError("single-symbol bookTicker response must be an object")
        return result

    def aggregate_trades(
        self,
        symbol: str,
        *,
        from_id: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 500,
    ) -> tuple[Mapping[str, Any], ...]:
        normalized = self._symbol(symbol)
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be in [1, 1000]")
        for name, value in (
            ("from_id", from_id),
            ("start_time_ms", start_time_ms),
            ("end_time_ms", end_time_ms),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if start_time_ms is not None and end_time_ms is not None and start_time_ms > end_time_ms:
            raise ValueError("start_time_ms must be <= end_time_ms")
        result = self._get_json(
            "/api/v3/aggTrades",
            {
                "symbol": normalized,
                "fromId": from_id,
                "startTime": start_time_ms,
                "endTime": end_time_ms,
                "limit": limit,
            },
        )
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise BinancePublicDataError("aggTrades response must be an array of objects")
        return tuple(result)

    def collect_book_ticker(self, symbol: str) -> EventRecord:
        raw = self.book_ticker(symbol)
        # Observation time is sampled only after the HTTP response has arrived.
        observed_at_ns = self.clock_ns()
        return normalize_rest_book_ticker(
            raw,
            observed_at_ns=observed_at_ns,
            expected_symbol=self._symbol(symbol),
        )

    def collect_aggregate_trades(
        self,
        symbol: str,
        *,
        from_id: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 500,
    ) -> tuple[EventRecord, ...]:
        rows = self.aggregate_trades(
            symbol,
            from_id=from_id,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            limit=limit,
        )
        # A REST batch becomes available to this process at response arrival.
        # Historical trade timestamps remain event_time only, never observed_at.
        observed_at_ns = self.clock_ns()
        normalized_symbol = self._symbol(symbol)
        return tuple(
            normalize_rest_agg_trade(row, symbol=normalized_symbol, observed_at_ns=observed_at_ns)
            for row in rows
        )
