from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, cast


class RpcError(RuntimeError):
    pass


@dataclass(slots=True)
class JsonRpcClient:
    url: str
    timeout_s: float = 20.0
    retries: int = 4
    backoff_s: float = 0.6
    _request_id: int = 0

    def call(self, method: str, params: list[object]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._request_id += 1
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
            ).encode()
            request = urllib.request.Request(  # noqa: S310  # nosec B310 - configured RPC URL
                self.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    request, timeout=self.timeout_s
                ) as response:
                    body = cast(dict[str, Any], json.loads(response.read()))
                if "error" in body:
                    raise RpcError(f"{method}: {body['error']}")
                return body.get("result")
            except (OSError, urllib.error.URLError, json.JSONDecodeError, RpcError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff_s * (2**attempt))
        raise RpcError(f"RPC request failed after {self.retries} attempts: {method}: {last_error}")

    def chain_id(self) -> int:
        return int(cast(str, self.call("eth_chainId", [])), 16)

    def block_number(self) -> int:
        return int(cast(str, self.call("eth_blockNumber", [])), 16)

    def block(self, number: int) -> dict[str, Any]:
        result = self.call("eth_getBlockByNumber", [hex(number), False])
        if result is None:
            raise RpcError(f"block not found: {number}")
        return cast(dict[str, Any], result)

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        filter_: dict[str, object] = {
            "address": address,
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
        }
        if topic0s:
            filter_["topics"] = [list(topic0s)]
        result = self.call("eth_getLogs", [filter_])
        return cast(list[dict[str, Any]], result)

    def get_code(self, address: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return cast(str, self.call("eth_getCode", [address, tag]))

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return cast(str, self.call("eth_call", [{"to": to, "data": data}, tag]))

    def balance(self, address: str, tag: str = "latest") -> int:
        return int(cast(str, self.call("eth_getBalance", [address, tag])), 16)


def _is_loopback_rpc_url(url: str) -> bool:
    from ipaddress import ip_address
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


@dataclass(slots=True)
class LocalForkRpcClient(JsonRpcClient):
    """Transaction-capable RPC adapter that refuses every non-loopback endpoint."""

    def __post_init__(self) -> None:
        if not _is_loopback_rpc_url(self.url):
            raise ValueError("local fork RPC must use a loopback hostname/address")

    def transaction_count(self, address: str, tag: str = "pending") -> int:
        return int(cast(str, self.call("eth_getTransactionCount", [address, tag])), 16)

    def transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        result = self.call("eth_getTransactionReceipt", [tx_hash])
        return None if result is None else cast(dict[str, Any], result)

    def impersonate_account(self, address: str) -> None:
        result = self.call("anvil_impersonateAccount", [address])
        if result is not True:
            raise RpcError("local fork refused account impersonation")

    def set_balance(self, address: str, value_wei: int) -> None:
        if value_wei < 0:
            raise ValueError("balance must be non-negative")
        result = self.call("anvil_setBalance", [address, hex(value_wei)])
        if result is not True:
            raise RpcError("local fork refused balance update")

    def send_transaction(
        self,
        *,
        from_address: str,
        to: str,
        data: str,
        value_wei: int,
        gas: int | None = None,
        gas_price_wei: int | None = None,
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
        if gas_price_wei is not None:
            tx["gasPrice"] = hex(gas_price_wei)
        if nonce is not None:
            tx["nonce"] = hex(nonce)
        return cast(str, self.call("eth_sendTransaction", [tx]))

    def mine(self) -> None:
        self.call("evm_mine", [])
