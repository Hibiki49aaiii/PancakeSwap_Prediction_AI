from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
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
        raw = self.transport(request, self.timeout_seconds)
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
