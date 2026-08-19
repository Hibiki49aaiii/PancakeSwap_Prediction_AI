from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


READ_ONLY_RPC_METHODS = frozenset(
    {
        "eth_chainId",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_getCode",
        "eth_call",
        "eth_getLogs",
    }
)
DEFAULT_USER_AGENT = "pancake-prediction-ai/0.7 read-only-json-rpc"


class RpcError(RuntimeError):
    pass


Transport = Callable[[Request, float], bytes]


def _default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is explicitly configured
        return response.read()


def _log_filter_range(params: list[Any]) -> tuple[int, int] | None:
    if len(params) != 1 or not isinstance(params[0], dict):
        return None
    filter_ = params[0]
    from_value = filter_.get("fromBlock")
    to_value = filter_.get("toBlock")
    if not isinstance(from_value, str) or not isinstance(to_value, str):
        return None
    if not from_value.startswith("0x") or not to_value.startswith("0x"):
        return None
    try:
        from_block = int(from_value, 16)
        to_block = int(to_value, 16)
    except ValueError:
        return None
    if from_block < 0 or to_block < from_block:
        return None
    return from_block, to_block


def _split_log_params(params: list[Any]) -> tuple[list[Any], list[Any]] | None:
    range_ = _log_filter_range(params)
    if range_ is None:
        return None
    from_block, to_block = range_
    if from_block >= to_block:
        return None
    middle = (from_block + to_block) // 2
    original = params[0]
    left_filter = dict(original)
    right_filter = dict(original)
    left_filter["fromBlock"] = hex(from_block)
    left_filter["toBlock"] = hex(middle)
    right_filter["fromBlock"] = hex(middle + 1)
    right_filter["toBlock"] = hex(to_block)
    return [left_filter], [right_filter]


def _looks_like_log_range_limit(error: object) -> bool:
    if not isinstance(error, dict):
        return False
    message = str(error.get("message", "")).lower()
    return any(
        marker in message
        for marker in (
            "limited to",
            "block range",
            "range limit",
            "too many results",
            "too many logs",
            "response size",
            "query returned more",
            "please narrow",
        )
    )


@dataclass(slots=True)
class ReadOnlyJsonRpcClient:
    endpoint: str
    timeout_seconds: float = 10.0
    transport: Transport = _default_transport
    _next_id: int = 1

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("JSON-RPC endpoint must be an http(s) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def _retry_split_logs(self, params: list[Any]) -> list[Any] | None:
        split = _split_log_params(params)
        if split is None:
            return None
        left_params, right_params = split
        left = self.call("eth_getLogs", left_params)
        right = self.call("eth_getLogs", right_params)
        if not isinstance(left, list) or not isinstance(right, list):
            raise RpcError("eth_getLogs split responses must be arrays")
        return [*left, *right]

    def call(self, method: str, params: list[Any]) -> Any:
        if method not in READ_ONLY_RPC_METHODS:
            raise PermissionError(f"RPC method is outside read-only boundary: {method}")
        if not isinstance(params, list):
            raise ValueError("params must be a list")

        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        ).encode()
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            method="POST",
        )
        try:
            raw = self.transport(request, self.timeout_seconds)
        except HTTPError as exc:
            if method == "eth_getLogs" and exc.code == 403:
                split_result = self._retry_split_logs(params)
                if split_result is not None:
                    return split_result
            raise
        try:
            response = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RpcError("RPC response is not valid JSON") from exc
        if not isinstance(response, dict):
            raise RpcError("RPC response must be an object")
        if response.get("id") != request_id:
            raise RpcError("RPC response id mismatch")
        if "error" in response:
            error = response["error"]
            if method == "eth_getLogs" and _looks_like_log_range_limit(error):
                split_result = self._retry_split_logs(params)
                if split_result is not None:
                    return split_result
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                raise RpcError(f"RPC error {code}: {message}")
            raise RpcError(f"RPC error: {error}")
        if "result" not in response:
            raise RpcError("RPC response missing result")
        return response["result"]

    def chain_id(self) -> int:
        value = self.call("eth_chainId", [])
        if not isinstance(value, str) or not value.startswith("0x"):
            raise RpcError("eth_chainId result must be hex string")
        return int(value, 16)

    def block_number(self) -> int:
        value = self.call("eth_blockNumber", [])
        if not isinstance(value, str) or not value.startswith("0x"):
            raise RpcError("eth_blockNumber result must be hex string")
        return int(value, 16)
