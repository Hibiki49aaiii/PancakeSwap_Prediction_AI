from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.contracts import MARKETS
from pancake_prediction.historical_bootstrap import run_historical_bootstrap
from pancake_prediction.rpc import RpcError

ORACLE_PROXY = "0x" + "11" * 20
CHAINLINK_AGGREGATOR = "0x" + "22" * 20


class ProxyBootstrapRpc:
    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return 200

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "parentHash": "0x" + f"{max(0, number - 1):064x}",
            "timestamp": hex(1_700_000_000 + number),
        }

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        del address, from_block, to_block, topic0s
        return []

    def get_code(self, address: str, block: int | str = "latest") -> str:
        del address
        if block == "latest":
            return "0x6000"
        return "0x6000" if int(block) >= 100 else "0x"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del data, block
        address = (
            ORACLE_PROXY
            if to.lower() == MARKETS["BNBUSD"].address.lower()
            else CHAINLINK_AGGREGATOR
        )
        return "0x" + "00" * 12 + address[2:]


def test_historical_bootstrap_rejects_proxy_until_route_timeline_is_supported(
    tmp_path: Path,
) -> None:
    with pytest.raises(RpcError, match="proxy/aggregator route-timeline"):
        run_historical_bootstrap(
            ProxyBootstrapRpc(),
            MARKETS["BNBUSD"],
            tmp_path / "history.sqlite3",
            confirmations=64,
        )
