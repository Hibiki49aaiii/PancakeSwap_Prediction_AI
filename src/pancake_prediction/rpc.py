from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, cast

from pancake_prediction import __version__

JSON_RPC_USER_AGENT = f"pancakeswap-prediction-ai/{__version__}"


class RpcError(RuntimeError):
    pass


class RpcResponseError(RpcError):
    """A JSON-RPC application error returned by the provider.

    These errors are deterministic for the submitted request and are surfaced
    immediately so higher layers (for example the historical log collector)
    can adapt the request instead of sleeping and retrying the exact same
    failing query several times.
    """

    def __init__(self, method: str, error: object) -> None:
        self.method = method
        self.code: int | None = None
        self.data: object | None = None
        if isinstance(error, dict):
            raw_code = error.get("code")
            if isinstance(raw_code, int):
                self.code = raw_code
            message = str(error.get("message", error))
            self.data = error.get("data")
        else:
            message = str(error)
        code_text = "" if self.code is None else f" code={self.code}"
        super().__init__(f"{method}: JSON-RPC error{code_text}: {message}")


class RpcLogQueryError(RpcResponseError):
    """Preserve the exact failing eth_getLogs query after adaptive splitting."""

    def __init__(
        self,
        response_error: RpcResponseError,
        *,
        address: str,
        from_block: int,
        to_block: int,
        topic0s: tuple[str, ...] | None,
    ) -> None:
        self.response_error = response_error
        self.method = response_error.method
        self.code = response_error.code
        self.data = response_error.data
        self.address = address.lower()
        self.from_block = from_block
        self.to_block = to_block
        self.topic0s = topic0s
        RpcError.__init__(
            self,
            f"eth_getLogs query failed address={self.address} "
            f"blocks={from_block}..{to_block} topics={0 if topic0s is None else len(topic0s)}: "
            f"{response_error}",
        )

    def as_dict(self) -> dict[str, object]:
        is_single_block = self.from_block == self.to_block
        return {
            "classification": (
                "PROVIDER_LOG_LIMIT"
                if self.code == -32005 and is_single_block
                else "RPC_LOG_ERROR"
            ),
            "rpc_code": self.code,
            "address": self.address,
            "from_block": self.from_block,
            "to_block": self.to_block,
            "range_blocks": self.to_block - self.from_block + 1,
            "topic0_count": 0 if self.topic0s is None else len(self.topic0s),
            "topic0s": [] if self.topic0s is None else list(self.topic0s),
            "single_block_reached": is_single_block,
            "provider_message": str(self.response_error),
        }


@dataclass(slots=True)
class JsonRpcClient:
    url: str
    timeout_s: float = 20.0
    retries: int = 4
    backoff_s: float = 0.6
    min_interval_s: float = 0.0
    _request_id: int = 0
    _last_request_at: float | None = field(default=None, init=False, repr=False)

    def _pace_request(self) -> None:
        if self.min_interval_s < 0:
            raise ValueError("min_interval_s must be non-negative")
        now = time.monotonic()
        if self._last_request_at is not None:
            wait_s = self.min_interval_s - (now - self._last_request_at)
            if wait_s > 0:
                time.sleep(wait_s)
                now += wait_s
        self._last_request_at = now

    @staticmethod
    def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
        if exc.code != 429 or exc.headers is None:
            return None
        value = exc.headers.get("Retry-After")
        if value is None:
            return None
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, seconds)

    def call(self, method: str, params: list[object]) -> Any:
        if self.retries < 1:
            raise ValueError("retries must be positive")
        if self.backoff_s < 0:
            raise ValueError("backoff_s must be non-negative")
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._pace_request()
            self._request_id += 1
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": method,
                    "params": params,
                }
            ).encode()
            request = urllib.request.Request(  # noqa: S310  # nosec B310
                self.url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": JSON_RPC_USER_AGENT,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    request, timeout=self.timeout_s
                ) as response:
                    decoded = json.loads(response.read())
                if not isinstance(decoded, dict):
                    raise RpcError(f"{method}: malformed JSON-RPC response")
                body = cast(dict[str, Any], decoded)
                if "error" in body:
                    raise RpcResponseError(method, body["error"])
                return body.get("result")
            except RpcResponseError:
                raise
            except RpcError:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.fp is not None:
                    try:
                        decoded_error = json.loads(exc.read())
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        decoded_error = None
                    if isinstance(decoded_error, dict) and "error" in decoded_error:
                        raise RpcResponseError(method, decoded_error["error"]) from exc
                last_error = exc
                if attempt + 1 < self.retries:
                    retry_after_s = self._retry_after_seconds(exc)
                    exponential_s = self.backoff_s * (2**attempt)
                    time.sleep(max(exponential_s, retry_after_s or 0.0))
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff_s * (2**attempt))
        raise RpcError(
            f"RPC request failed after {self.retries} attempts: {method}: {last_error}"
        )

    def chain_id(self) -> int:
        return int(cast(str, self.call("eth_chainId", [])), 16)

    def block_number(self) -> int:
        return int(cast(str, self.call("eth_blockNumber", [])), 16)

    def block(self, number: int) -> dict[str, Any]:
        result = self.call("eth_getBlockByNumber", [hex(number), False])
        if result is None:
            raise RpcError(f"block not found: {number}")
        return cast(dict[str, Any], result)

    def transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        result = self.call("eth_getTransactionByHash", [tx_hash])
        return None if result is None else cast(dict[str, Any], result)

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
        try:
            result = self.call("eth_getLogs", [filter_])
        except RpcResponseError as exc:
            raise RpcLogQueryError(
                exc,
                address=address,
                from_block=from_block,
                to_block=to_block,
                topic0s=topic0s,
            ) from exc
        return cast(list[dict[str, Any]], result)

    def get_code(self, address: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return cast(str, self.call("eth_getCode", [address, tag]))

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        return cast(str, self.call("eth_call", [{"to": to, "data": data}, tag]))

    def balance(self, address: str, tag: int | str = "latest") -> int:
        block_tag = hex(tag) if isinstance(tag, int) else tag
        return int(cast(str, self.call("eth_getBalance", [address, block_tag])), 16)


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

    @property
    def fork_only(self) -> bool:
        return True

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
        if result is not None and result is not True:
            raise RpcError("local fork refused account impersonation")

    def stop_impersonating_account(self, address: str) -> None:
        result = self.call("anvil_stopImpersonatingAccount", [address])
        if result is not None and result is not True:
            raise RpcError("local fork refused to stop account impersonation")

    def set_balance(self, address: str, value_wei: int) -> None:
        if value_wei < 0:
            raise ValueError("balance must be non-negative")
        result = self.call("anvil_setBalance", [address, hex(value_wei)])
        if result is not None and result is not True:
            raise RpcError("local fork refused balance update")

    def set_automine(self, enabled: bool) -> None:
        self.call("anvil_setAutomine", [enabled])

    def drop_transaction(self, tx_hash: str) -> None:
        result = self.call("anvil_dropTransaction", [tx_hash])
        if result is None:
            raise RpcError("local fork did not find the requested transaction to drop")
        dropped_hash = str(result).lower()
        if dropped_hash != tx_hash.lower():
            raise RpcError("local fork returned an unexpected dropped transaction hash")

    def reorg(self, depth: int = 1) -> None:
        if depth < 1:
            raise ValueError("reorg depth must be positive")
        result = self.call(
            "anvil_reorg",
            [{"depth": depth, "tx_block_pairs": []}],
        )
        if result is not None and result is not True:
            raise RpcError("local fork refused reorg injection")

    def snapshot(self) -> str:
        return str(self.call("anvil_snapshot", []))

    def revert(self, snapshot_id: str) -> None:
        result = self.call("anvil_revert", [snapshot_id])
        if result is not True:
            raise RpcError("local fork refused snapshot revert")

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
