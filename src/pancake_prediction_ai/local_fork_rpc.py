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
        "eth_call",
        "eth_getTransactionReceipt",
        "eth_sendTransaction",
        "evm_mine",
        "anvil_reset",
        "anvil_impersonateAccount",
        "anvil_stopImpersonatingAccount",
        "anvil_setBalance",
    }
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class LocalForkRpcError(RuntimeError):
    pass


Transport = Callable[[Request, float], bytes]


def _default_transport(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback endpoint is validated
        return response.read()


@dataclass(slots=True)
class LocalForkJsonRpcClient:
    """JSON-RPC client for a loopback-only local development fork.

    The client may submit an unsigned `eth_sendTransaction` only to a loopback
    development node, using an account unlocked/impersonated by that node. It
    never accepts a private key and never permits raw signed transactions or
    signing RPC methods. An upstream/mainnet RPC must use the separate read-only
    client and therefore cannot receive these local execution calls.
    """

    endpoint: str
    timeout_seconds: float = 10.0
    transport: Transport = _default_transport
    _next_id: int = 1

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("local fork JSON-RPC endpoint must be an http(s) URL")
        if parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError("local fork JSON-RPC endpoint must use a loopback host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("local fork JSON-RPC endpoint must not contain credentials")
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
                data = error.get("data")
                suffix = f" data={data}" if data is not None else ""
                raise LocalForkRpcError(f"RPC error {code}: {message}{suffix}")
            raise LocalForkRpcError(f"RPC error: {error}")
        if "result" not in response:
            raise LocalForkRpcError("RPC response missing result")
        return response["result"]
