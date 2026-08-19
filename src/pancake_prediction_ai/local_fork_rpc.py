from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


LOCAL_FORK_RPC_METHODS = frozenset(
    {
        "eth_chainId",
        "eth_blockNumber",
        "eth_getBlockByNumber",
        "eth_getCode",
        "evm_mine",
        "anvil_reset",
    }
)


class LocalForkRpcError(RuntimeError):
    pass


Transport = Callable[[Request, float], bytes]


def _default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is explicitly configured
        return response.read()


@dataclass(slots=True)
class LocalForkJsonRpcClient:
    """JSON-RPC client restricted to local fork inspection/mutation methods.

    The allowlist intentionally excludes account access, signing, transaction
    creation and transaction broadcasting. The only mutating calls are Anvil/
    EVM development-node controls used to mine one local block and reset the
    local fork. Pointing this client at a mainnet RPC does not enable broadcast.
    """

    endpoint: str
    timeout_seconds: float = 10.0
    transport: Transport = _default_transport
    _next_id: int = 1

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("local fork JSON-RPC endpoint must be an http(s) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def call(self, method: str, params: list[Any]) -> Any:
        if method not in LOCAL_FORK_RPC_METHODS:
            raise PermissionError(f"RPC method is outside local-fork boundary: {method}")
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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        raw = self.transport(request, self.timeout_seconds)
        try:
            response = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LocalForkRpcError("RPC response is not valid JSON") from exc
        if not isinstance(response, dict):
            raise LocalForkRpcError("RPC response must be an object")
        if response.get("id") != request_id:
            raise LocalForkRpcError("RPC response id mismatch")
        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message")
                raise LocalForkRpcError(f"RPC error {code}: {message}")
            raise LocalForkRpcError(f"RPC error: {error}")
        if "result" not in response:
            raise LocalForkRpcError("RPC response missing result")
        return response["result"]
