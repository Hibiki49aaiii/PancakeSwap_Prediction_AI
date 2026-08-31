from typing import Any

import pytest

from pancake_prediction.contracts import MARKETS
from pancake_prediction.historical_preflight import (
    discover_deployment_block,
    run_historical_preflight,
)
from pancake_prediction.rpc import RpcError


class FakeArchiveRpc:
    def __init__(self, *, archive_available: bool = True) -> None:
        self.archive_available = archive_available

    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return 200

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "timestamp": hex(1_700_000_000 + number),
        }

    def get_code(self, address: str, block: int | str = "latest") -> str:
        del address
        if block == "latest":
            return "0x6000"
        block_number = int(block)
        return "0x6000" if block_number >= 100 else "0x"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to, data
        if block != "latest" and int(block) == 100 and not self.archive_available:
            raise RpcError("missing trie node")
        oracle = "11" * 20
        return "0x" + "00" * 12 + oracle


def test_deployment_discovery_finds_first_code_block() -> None:
    rpc = FakeArchiveRpc()
    assert discover_deployment_block(rpc, MARKETS["BNBUSD"].address, upper_block=200) == 100


def test_historical_preflight_probes_exact_deployment_block() -> None:
    result = run_historical_preflight(FakeArchiveRpc(), MARKETS["BNBUSD"])
    assert result.head_block == 200
    assert result.deployment_block == 100
    assert result.archive_probe.block_number == 100
    assert result.archive_probe.chain_id == 56


def test_historical_preflight_rejects_non_archive_rpc() -> None:
    with pytest.raises(RpcError, match="archive-capable"):
        run_historical_preflight(
            FakeArchiveRpc(archive_available=False),
            MARKETS["BNBUSD"],
        )
