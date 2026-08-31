from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast

from .rpc import RpcError


class ExplorerApiError(RpcError):
    """Explorer API failure with credentials redacted from all error text."""


class CanonicalRpc(Protocol):
    def chain_id(self) -> int: ...
    def block_number(self) -> int: ...
    def block(self, number: int) -> dict[str, Any]: ...
    def get_code(self, address: str, block: int | str = "latest") -> str: ...
    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str: ...


@dataclass(frozen=True, slots=True)
class ExplorerLogQueryEvidence:
    provider: str
    chain_id: int
    address: str
    from_block: int
    to_block: int
    topic0s: tuple[str, ...] | None
    page_size: int
    pages_requested: int
    records_received: int
    unique_records: int

    def payload(self) -> dict[str, object]:
        return asdict(self)

    @property
    def digest(self) -> str:
        raw = (
            json.dumps(
                self.payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode()
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, object]:
        payload = self.payload()
        payload["query_digest"] = self.digest
        return payload


def _normalize_hex_quantity(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ExplorerApiError(f"explorer log {field} must be 0x-prefixed hex")
    raw = value[2:] or "0"
    try:
        integer = int(raw, 16)
    except ValueError as exc:
        raise ExplorerApiError(f"explorer log {field} is not valid hex") from exc
    return hex(integer)


def _require_hex(value: object, *, field: str, bytes_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ExplorerApiError(f"explorer log {field} must be 0x-prefixed hex")
    raw = value[2:].lower()
    if bytes_length is not None and len(raw) != bytes_length * 2:
        raise ExplorerApiError(f"explorer log {field} has invalid length")
    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        raise ExplorerApiError(f"explorer log {field} is not valid hex") from exc
    return "0x" + raw


def normalize_explorer_log(
    raw: dict[str, Any],
    *,
    expected_address: str,
    expected_topic0: str | None,
) -> dict[str, Any]:
    address = _require_hex(raw.get("address"), field="address", bytes_length=20)
    if address.lower() != expected_address.lower():
        raise ExplorerApiError("explorer returned a log for an unexpected address")

    raw_topics = raw.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        raise ExplorerApiError("explorer log topics must be a non-empty list")
    topics = [
        _require_hex(topic, field=f"topics[{index}]", bytes_length=32)
        for index, topic in enumerate(raw_topics)
    ]
    if expected_topic0 is not None and topics[0].lower() != expected_topic0.lower():
        raise ExplorerApiError("explorer returned a log for an unexpected topic0")

    return {
        "address": address,
        "topics": topics,
        "data": _require_hex(raw.get("data", "0x"), field="data"),
        "blockNumber": _normalize_hex_quantity(raw.get("blockNumber"), field="blockNumber"),
        "blockHash": _require_hex(raw.get("blockHash"), field="blockHash", bytes_length=32),
        "transactionHash": _require_hex(
            raw.get("transactionHash"),
            field="transactionHash",
            bytes_length=32,
        ),
        "transactionIndex": _normalize_hex_quantity(
            raw.get("transactionIndex"),
            field="transactionIndex",
        ),
        "logIndex": _normalize_hex_quantity(raw.get("logIndex"), field="logIndex"),
        "removed": False,
    }


class EtherscanV2LogsClient:
    """Read-only Etherscan V2 log transport.

    The API key is used only while constructing the outbound request and is never
    included in evidence, repr output, or raised transport errors.
    """

    provider = "etherscan-v2"

    def __init__(
        self,
        api_key: str,
        *,
        chain_id: int = 56,
        base_url: str = "https://api.etherscan.io/v2/api",
        page_size: int = 1_000,
        timeout_s: float = 20.0,
        retries: int = 3,
        backoff_s: float = 0.5,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Etherscan API key is required")
        if chain_id <= 0:
            raise ValueError("chain_id must be positive")
        if not 1 <= page_size <= 1_000:
            raise ValueError("page_size must be in [1, 1000]")
        if timeout_s <= 0 or retries < 1 or backoff_s < 0:
            raise ValueError("invalid Etherscan client retry configuration")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Etherscan base_url must be an absolute HTTPS URL")
        self._api_key = api_key
        self.chain_id = chain_id
        self.base_url = base_url
        self.page_size = page_size
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._evidence: list[ExplorerLogQueryEvidence] = []

    def __repr__(self) -> str:
        return (
            "EtherscanV2LogsClient("
            f"chain_id={self.chain_id}, base_url={self.base_url!r}, "
            f"page_size={self.page_size})"
        )

    @property
    def evidence(self) -> tuple[ExplorerLogQueryEvidence, ...]:
        return tuple(self._evidence)

    def evidence_manifest(self) -> dict[str, object]:
        queries = [item.as_dict() for item in self._evidence]
        raw = (
            json.dumps(queries, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            + "\n"
        ).encode()
        return {
            "provider": self.provider,
            "chain_id": self.chain_id,
            "base_url": self.base_url,
            "credential_persisted": False,
            "query_count": len(queries),
            "queries_digest": hashlib.sha256(raw).hexdigest(),
            "queries": queries,
        }

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "<redacted>")[:300]

    def _request_page(self, params: dict[str, str]) -> list[dict[str, Any]]:
        request_params = {**params, "apikey": self._api_key}
        url = self.base_url + "?" + urllib.parse.urlencode(request_params)
        request = urllib.request.Request(  # noqa: S310  # nosec B310
            url,
            headers={"Accept": "application/json", "User-Agent": "pcs-prediction-research/0.7"},
            method="GET",
        )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    request,
                    timeout=self.timeout_s,
                ) as response:
                    decoded = json.loads(response.read())
                if not isinstance(decoded, dict):
                    raise ExplorerApiError("Etherscan returned a malformed JSON object")
                body = cast(dict[str, Any], decoded)
                result = body.get("result")
                status = str(body.get("status", ""))
                message = str(body.get("message", ""))
                if status == "1" and isinstance(result, list):
                    return [cast(dict[str, Any], row) for row in result if isinstance(row, dict)]
                if status == "0" and result == [] and "no" in message.lower():
                    return []
                detail = self._redact(str(result))
                raise ExplorerApiError(f"Etherscan API rejected log query: {detail}")
            except ExplorerApiError:
                raise
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff_s * (2**attempt))
        error_type = type(last_error).__name__ if last_error is not None else "unknown"
        raise ExplorerApiError(
            f"Etherscan request failed after {self.retries} attempts: {error_type}"
        )

    def _fetch_one_topic(
        self,
        *,
        address: str,
        from_block: int,
        to_block: int,
        topic0: str | None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        records: list[dict[str, Any]] = []
        page = 1
        pages_requested = 0
        while True:
            params = {
                "chainid": str(self.chain_id),
                "module": "logs",
                "action": "getLogs",
                "address": address,
                "fromBlock": str(from_block),
                "toBlock": str(to_block),
                "page": str(page),
                "offset": str(self.page_size),
            }
            if topic0 is not None:
                params["topic0"] = topic0
            raw_page = self._request_page(params)
            pages_requested += 1
            records.extend(
                normalize_explorer_log(
                    row,
                    expected_address=address,
                    expected_topic0=topic0,
                )
                for row in raw_page
            )
            if len(raw_page) < self.page_size:
                break
            page += 1
        return records, pages_requested, len(records)

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if from_block < 0 or to_block < from_block:
            raise ValueError("invalid explorer log block range")
        normalized_address = _require_hex(address, field="address", bytes_length=20)
        normalized_topics = None
        if topic0s is not None:
            if not topic0s:
                return []
            normalized_topics = tuple(
                _require_hex(topic, field="topic0", bytes_length=32) for topic in topic0s
            )

        partitions = (None,) if normalized_topics is None else normalized_topics
        received = 0
        pages = 0
        unique: dict[tuple[str, str, str], dict[str, Any]] = {}
        for topic0 in partitions:
            rows, partition_pages, partition_received = self._fetch_one_topic(
                address=normalized_address,
                from_block=from_block,
                to_block=to_block,
                topic0=topic0,
            )
            pages += partition_pages
            received += partition_received
            for row in rows:
                key = (
                    str(row["blockHash"]).lower(),
                    str(row["transactionHash"]).lower(),
                    str(row["logIndex"]).lower(),
                )
                unique[key] = row

        ordered = sorted(
            unique.values(),
            key=lambda item: (
                int(str(item["blockNumber"]), 16),
                int(str(item["transactionIndex"]), 16),
                int(str(item["logIndex"]), 16),
            ),
        )
        self._evidence.append(
            ExplorerLogQueryEvidence(
                provider=self.provider,
                chain_id=self.chain_id,
                address=normalized_address.lower(),
                from_block=from_block,
                to_block=to_block,
                topic0s=(
                    None
                    if normalized_topics is None
                    else tuple(topic.lower() for topic in normalized_topics)
                ),
                page_size=self.page_size,
                pages_requested=pages,
                records_received=received,
                unique_records=len(ordered),
            )
        )
        return ordered


@dataclass(slots=True)
class HybridExplorerRpc:
    """Use an explorer only for log transport and an RPC for canonical state/headers."""

    canonical_rpc: CanonicalRpc
    explorer_logs: EtherscanV2LogsClient

    def chain_id(self) -> int:
        chain_id = self.canonical_rpc.chain_id()
        if chain_id != self.explorer_logs.chain_id:
            raise RpcError(
                "canonical RPC chain id does not match explorer log source chain id"
            )
        return chain_id

    def block_number(self) -> int:
        return self.canonical_rpc.block_number()

    def block(self, number: int) -> dict[str, Any]:
        return self.canonical_rpc.block(number)

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        return self.explorer_logs.get_logs(
            address,
            from_block,
            to_block,
            topic0s=topic0s,
        )

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return self.canonical_rpc.get_code(address, block)

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        return self.canonical_rpc.eth_call(to, data, block)
