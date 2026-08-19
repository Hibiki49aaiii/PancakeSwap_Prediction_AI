from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PREDICTION_V2_SUBGRAPH_ID = "4kRuZVKCR9dsG2ePXhLSiKw5oaw3YMJo4nAwxZbUaqVY"
THE_GRAPH_GATEWAY_BASE_URL = "https://gateway.thegraph.com/api"
PREDICTION_V2_SUBGRAPH_URL = (
    f"{THE_GRAPH_GATEWAY_BASE_URL}/subgraphs/id/{PREDICTION_V2_SUBGRAPH_ID}"
)
DEFAULT_USER_AGENT = "pancake-prediction-ai/0.7 prediction-subgraph"


class PredictionSubgraphError(RuntimeError):
    pass


HttpPost = Callable[[Request, float], bytes]


def _default_http_post(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed/configured HTTPS endpoint
        return response.read()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise PredictionSubgraphError(f"{field} must be an integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise PredictionSubgraphError(f"{field} must be an integer") from exc
    if parsed <= 0:
        raise PredictionSubgraphError(f"{field} must be positive")
    return parsed


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise PredictionSubgraphError(f"{field} must be an integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise PredictionSubgraphError(f"{field} must be an integer") from exc
    if parsed < 0:
        raise PredictionSubgraphError(f"{field} must be non-negative")
    return parsed


def _optional_positive_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, field)


def _decimal(value: object, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PredictionSubgraphError(f"{field} must be a decimal") from exc
    if not parsed.is_finite():
        raise PredictionSubgraphError(f"{field} must be finite")
    return parsed


def bnb_decimal_to_wei(value: object, field: str = "amount") -> int:
    """Convert the official Prediction subgraph's 18-decimal BNB value to wei."""

    parsed = _decimal(value, field)
    if parsed < 0:
        raise PredictionSubgraphError(f"{field} must be non-negative")
    wei = parsed * Decimal(10**18)
    integral = wei.to_integral_value()
    if wei != integral:
        raise PredictionSubgraphError(f"{field} has sub-wei precision")
    return int(integral)


def _hex_bytes(value: object, field: str, *, length: int | None = None) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise PredictionSubgraphError(f"{field} must be hex")
    raw = value[2:]
    if length is not None and len(raw) != length * 2:
        raise PredictionSubgraphError(f"{field} must be {length} bytes")
    try:
        int(raw or "0", 16)
    except ValueError as exc:
        raise PredictionSubgraphError(f"{field} must be hex") from exc
    return value.lower()


@dataclass(frozen=True, slots=True)
class PredictionSubgraphMeta:
    block_number: int
    block_hash: str | None
    has_indexing_errors: bool


@dataclass(frozen=True, slots=True)
class PredictionSubgraphRound:
    id: str
    epoch: int
    start_at_s: int
    start_block: int
    start_hash: str
    lock_at_s: int | None
    lock_block: int | None
    lock_hash: str | None
    lock_price: Decimal | None
    lock_round_id: int | None
    close_at_s: int | None
    close_block: int | None
    close_hash: str | None
    close_price: Decimal | None
    close_round_id: int | None
    total_bets: int
    total_amount_wei: int
    bull_bets: int
    bull_amount_wei: int
    bear_bets: int
    bear_amount_wei: int
    position: str | None
    failed: bool | None

    @property
    def complete(self) -> bool:
        return (
            self.failed is False
            and self.lock_at_s is not None
            and self.lock_block is not None
            and self.lock_price is not None
            and self.lock_round_id is not None
            and self.close_at_s is not None
            and self.close_block is not None
            and self.close_price is not None
            and self.close_round_id is not None
        )


@dataclass(frozen=True, slots=True)
class PredictionSubgraphBet:
    id: str
    epoch: int
    user: str
    transaction_hash: str
    amount_wei: int
    position: str
    created_at_s: int
    updated_at_s: int
    block_number: int


_META_QUERY = """
query PredictionMeta {
  _meta { block { number hash } hasIndexingErrors }
}
"""

_ROUNDS_QUERY = """
query PredictionRounds($first: Int!, $after: BigInt!, $through: BigInt!) {
  rounds(
    first: $first
    orderBy: epoch
    orderDirection: asc
    where: { epoch_gt: $after, epoch_lte: $through }
  ) {
    id epoch position failed
    startAt startBlock startHash
    lockAt lockBlock lockHash lockPrice lockRoundId
    closeAt closeBlock closeHash closePrice closeRoundId
    totalBets totalAmount bullBets bullAmount bearBets bearAmount
  }
}
"""

_BETS_QUERY = """
query PredictionBets($first: Int!, $round: String!, $after: ID!) {
  bets(
    first: $first
    orderBy: id
    orderDirection: asc
    where: { round: $round, id_gt: $after }
  ) {
    id
    round { id epoch }
    user { id }
    hash amount position createdAt updatedAt block
  }
}
"""


@dataclass(slots=True)
class PredictionSubgraphClient:
    """Read-only client for PancakeSwap's official Prediction V2 Subgraph.

    The API key is sent only in the Authorization header. It is never embedded
    in the endpoint, query variables, returned dataclasses, or exception text.
    """

    api_key: str
    endpoint: str = PREDICTION_V2_SUBGRAPH_URL
    timeout_seconds: float = 20.0
    http_post: HttpPost = _default_http_post

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise ValueError("The Graph API key is required")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Prediction subgraph endpoint must be HTTPS")
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("Prediction subgraph endpoint must not contain credentials/query/fragment")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key = self.api_key.strip()
        self.endpoint = self.endpoint.rstrip("/")

    def _query(self, query: str, variables: Mapping[str, object] | None = None) -> Mapping[str, Any]:
        payload = json.dumps(
            {"query": query, "variables": dict(variables or {})},
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            method="POST",
        )
        try:
            raw = self.http_post(request, self.timeout_seconds)
        except Exception as exc:
            raise PredictionSubgraphError(
                f"Prediction subgraph request failed: {type(exc).__name__}"
            ) from exc
        try:
            document = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PredictionSubgraphError("Prediction subgraph response is not valid JSON") from exc
        if not isinstance(document, dict):
            raise PredictionSubgraphError("Prediction subgraph response must be an object")
        errors = document.get("errors")
        if errors:
            if isinstance(errors, list):
                messages = []
                for item in errors[:3]:
                    if isinstance(item, dict):
                        messages.append(str(item.get("message", "GraphQL error")))
                    else:
                        messages.append("GraphQL error")
                detail = "; ".join(messages)
            else:
                detail = "GraphQL error"
            raise PredictionSubgraphError(f"Prediction subgraph GraphQL error: {detail}")
        data = document.get("data")
        if not isinstance(data, dict):
            raise PredictionSubgraphError("Prediction subgraph response missing data")
        return data

    def meta(self) -> PredictionSubgraphMeta:
        data = self._query(_META_QUERY)
        meta = data.get("_meta")
        if not isinstance(meta, dict):
            raise PredictionSubgraphError("Prediction subgraph _meta is unavailable")
        block = meta.get("block")
        if not isinstance(block, dict):
            raise PredictionSubgraphError("Prediction subgraph _meta.block is unavailable")
        block_hash_raw = block.get("hash")
        block_hash = None if block_hash_raw is None else _hex_bytes(block_hash_raw, "_meta.block.hash", length=32)
        has_errors = meta.get("hasIndexingErrors")
        if not isinstance(has_errors, bool):
            raise PredictionSubgraphError("Prediction subgraph indexing-error flag is invalid")
        return PredictionSubgraphMeta(
            block_number=_non_negative_int(block.get("number"), "_meta.block.number"),
            block_hash=block_hash,
            has_indexing_errors=has_errors,
        )

    def rounds(
        self,
        *,
        from_epoch: int,
        to_epoch: int,
        page_size: int = 500,
    ) -> tuple[PredictionSubgraphRound, ...]:
        if from_epoch < 0 or to_epoch < from_epoch:
            raise ValueError("invalid Prediction subgraph epoch range")
        if not 1 <= page_size <= 1_000:
            raise ValueError("page_size must be in [1, 1000]")
        after = from_epoch - 1
        rows: list[PredictionSubgraphRound] = []
        while True:
            data = self._query(
                _ROUNDS_QUERY,
                {"first": page_size, "after": str(after), "through": str(to_epoch)},
            )
            raw_rows = data.get("rounds")
            if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
                raise PredictionSubgraphError("Prediction subgraph rounds response is invalid")
            if not raw_rows:
                break
            page = tuple(_parse_round(row) for row in raw_rows)
            if any(item.epoch <= after or item.epoch > to_epoch for item in page):
                raise PredictionSubgraphError("Prediction subgraph round page violates epoch cursor")
            if any(current.epoch <= previous.epoch for previous, current in zip(page, page[1:])):
                raise PredictionSubgraphError("Prediction subgraph round page is not strictly ordered")
            rows.extend(page)
            after = page[-1].epoch
            if len(page) < page_size or after >= to_epoch:
                break
        epochs = [item.epoch for item in rows]
        if len(set(epochs)) != len(epochs):
            raise PredictionSubgraphError("Prediction subgraph returned duplicate epochs")
        return tuple(rows)

    def bets_for_round(
        self,
        round_id: str,
        *,
        expected_epoch: int | None = None,
        page_size: int = 500,
    ) -> tuple[PredictionSubgraphBet, ...]:
        if not round_id:
            raise ValueError("round_id is required")
        if expected_epoch is not None and expected_epoch < 0:
            raise ValueError("expected_epoch must be non-negative")
        if not 1 <= page_size <= 1_000:
            raise ValueError("page_size must be in [1, 1000]")
        after = ""
        rows: list[PredictionSubgraphBet] = []
        while True:
            data = self._query(
                _BETS_QUERY,
                {"first": page_size, "round": round_id, "after": after},
            )
            raw_rows = data.get("bets")
            if not isinstance(raw_rows, list) or any(not isinstance(row, dict) for row in raw_rows):
                raise PredictionSubgraphError("Prediction subgraph bets response is invalid")
            if not raw_rows:
                break
            page = tuple(_parse_bet(row) for row in raw_rows)
            ids = [item.id for item in page]
            if any(value <= after for value in ids):
                raise PredictionSubgraphError("Prediction subgraph bet page violates ID cursor")
            if any(current <= previous for previous, current in zip(ids, ids[1:])):
                raise PredictionSubgraphError("Prediction subgraph bet page is not strictly ordered")
            if expected_epoch is not None and any(item.epoch != expected_epoch for item in page):
                raise PredictionSubgraphError("Prediction subgraph bet belongs to unexpected epoch")
            rows.extend(page)
            after = ids[-1]
            if len(page) < page_size:
                break
        ids = [item.id for item in rows]
        if len(set(ids)) != len(ids):
            raise PredictionSubgraphError("Prediction subgraph returned duplicate bet IDs")
        return tuple(rows)


def _parse_round(row: Mapping[str, Any]) -> PredictionSubgraphRound:
    identifier = row.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise PredictionSubgraphError("round.id is invalid")
    position = row.get("position")
    if position is not None and position not in {"Bull", "Bear", "House"}:
        raise PredictionSubgraphError("round.position is invalid")
    failed = row.get("failed")
    if failed is not None and not isinstance(failed, bool):
        raise PredictionSubgraphError("round.failed is invalid")
    lock_hash_raw = row.get("lockHash")
    close_hash_raw = row.get("closeHash")
    lock_price_raw = row.get("lockPrice")
    close_price_raw = row.get("closePrice")
    result = PredictionSubgraphRound(
        id=identifier,
        epoch=_non_negative_int(row.get("epoch"), "round.epoch"),
        start_at_s=_positive_int(row.get("startAt"), "round.startAt"),
        start_block=_positive_int(row.get("startBlock"), "round.startBlock"),
        start_hash=_hex_bytes(row.get("startHash"), "round.startHash", length=32),
        lock_at_s=_optional_positive_int(row.get("lockAt"), "round.lockAt"),
        lock_block=_optional_positive_int(row.get("lockBlock"), "round.lockBlock"),
        lock_hash=None if lock_hash_raw is None else _hex_bytes(lock_hash_raw, "round.lockHash", length=32),
        lock_price=None if lock_price_raw is None else _decimal(lock_price_raw, "round.lockPrice"),
        lock_round_id=_optional_positive_int(row.get("lockRoundId"), "round.lockRoundId"),
        close_at_s=_optional_positive_int(row.get("closeAt"), "round.closeAt"),
        close_block=_optional_positive_int(row.get("closeBlock"), "round.closeBlock"),
        close_hash=None if close_hash_raw is None else _hex_bytes(close_hash_raw, "round.closeHash", length=32),
        close_price=None if close_price_raw is None else _decimal(close_price_raw, "round.closePrice"),
        close_round_id=_optional_positive_int(row.get("closeRoundId"), "round.closeRoundId"),
        total_bets=_non_negative_int(row.get("totalBets"), "round.totalBets"),
        total_amount_wei=bnb_decimal_to_wei(row.get("totalAmount"), "round.totalAmount"),
        bull_bets=_non_negative_int(row.get("bullBets"), "round.bullBets"),
        bull_amount_wei=bnb_decimal_to_wei(row.get("bullAmount"), "round.bullAmount"),
        bear_bets=_non_negative_int(row.get("bearBets"), "round.bearBets"),
        bear_amount_wei=bnb_decimal_to_wei(row.get("bearAmount"), "round.bearAmount"),
        position=position,
        failed=failed,
    )
    if result.total_bets != result.bull_bets + result.bear_bets:
        raise PredictionSubgraphError("round bet counts do not reconcile")
    if result.total_amount_wei != result.bull_amount_wei + result.bear_amount_wei:
        raise PredictionSubgraphError("round amounts do not reconcile")
    return result


def _parse_bet(row: Mapping[str, Any]) -> PredictionSubgraphBet:
    identifier = row.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise PredictionSubgraphError("bet.id is invalid")
    round_value = row.get("round")
    user_value = row.get("user")
    if not isinstance(round_value, dict) or not isinstance(user_value, dict):
        raise PredictionSubgraphError("bet round/user relation is invalid")
    user = user_value.get("id")
    if not isinstance(user, str) or not user.startswith("0x") or len(user) != 42:
        raise PredictionSubgraphError("bet.user.id is invalid")
    position = row.get("position")
    if position not in {"Bull", "Bear"}:
        raise PredictionSubgraphError("bet.position must be Bull or Bear")
    return PredictionSubgraphBet(
        id=identifier,
        epoch=_non_negative_int(round_value.get("epoch"), "bet.round.epoch"),
        user=user.lower(),
        transaction_hash=_hex_bytes(row.get("hash"), "bet.hash", length=32),
        amount_wei=bnb_decimal_to_wei(row.get("amount"), "bet.amount"),
        position=position,
        created_at_s=_positive_int(row.get("createdAt"), "bet.createdAt"),
        updated_at_s=_positive_int(row.get("updatedAt"), "bet.updatedAt"),
        block_number=_positive_int(row.get("block"), "bet.block"),
    )
