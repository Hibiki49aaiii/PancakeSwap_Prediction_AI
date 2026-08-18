from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import count
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class RpcError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RpcResponseError(RpcError):
    code: int
    message: str

    def __str__(self) -> str:
        return f"JSON-RPC error {self.code}: {self.message}"


class JsonRpcClient:
    """Small read-oriented JSON-RPC client with no signing/private-key support."""

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("RPC URL must be http(s) with a hostname")
        if parsed.username or parsed.password:
            raise ValueError("credentials in RPC URL are not allowed")
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._ids = count(1)

    def _request(self, method: str, params: list[object]) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        ).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                decoded = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RpcError(f"RPC request failed for {method}: {exc}") from exc

        if not isinstance(decoded, dict):
            raise RpcError("invalid JSON-RPC response")
        error = decoded.get("error")
        if isinstance(error, dict):
            raise RpcResponseError(int(error.get("code", -1)), str(error.get("message", "error")))
        if "result" not in decoded:
            raise RpcError("JSON-RPC response missing result")
        return decoded["result"]

    def chain_id(self) -> int:
        return int(str(self._request("eth_chainId", [])), 16)

    def block_number(self) -> int:
        return int(str(self._request("eth_blockNumber", [])), 16)

    def get_code(self, address: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return str(self._request("eth_getCode", [address, tag]))

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return str(self._request("eth_call", [{"to": to, "data": data}, tag]))


class LocalForkRpcClient(JsonRpcClient):
    """Transaction-capable RPC client that is structurally confined to loopback Anvil nodes."""

    _ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(url)
        if parsed.hostname not in self._ALLOWED_HOSTS:
            raise ValueError("transaction-capable fork RPC must use a loopback hostname")
        super().__init__(url, timeout_seconds=timeout_seconds)

    def impersonate_account(self, address: str) -> None:
        self._request("anvil_impersonateAccount", [address])

    def stop_impersonating_account(self, address: str) -> None:
        self._request("anvil_stopImpersonatingAccount", [address])

    def set_balance(self, address: str, value_wei: int) -> None:
        if value_wei < 0:
            raise ValueError("value_wei must be non-negative")
        self._request("anvil_setBalance", [address, hex(value_wei)])

    def send_transaction(
        self,
        *,
        from_address: str,
        to: str,
        data: str,
        value_wei: int,
        gas: int | None = None,
        nonce: int | None = None,
    ) -> str:
        tx: dict[str, str] = {
            "from": from_address,
            "to": to,
            "data": data,
            "value": hex(value_wei),
        }
        if gas is not None:
            tx["gas"] = hex(gas)
        if nonce is not None:
            tx["nonce"] = hex(nonce)
        return str(self._request("eth_sendTransaction", [tx]))

    def mine(self) -> None:
        self._request("anvil_mine", [1])
