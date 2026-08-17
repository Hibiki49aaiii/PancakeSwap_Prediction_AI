from typing import Any

import pytest

from pancake_prediction.contracts import MARKETS
from pancake_prediction.rpc import RpcError
from pancake_prediction.rpc_probe import probe_archive_state


class _ArchiveRpc:
    def __init__(
        self,
        *,
        chain_id: int = 56,
        fail_historical_call: bool = False,
        prediction_code: str = "0x6001",
        oracle_code: str = "0x6002",
    ) -> None:
        self._chain_id = chain_id
        self.fail_historical_call = fail_historical_call
        self.prediction_code = prediction_code
        self.oracle_code = oracle_code

    def chain_id(self) -> int:
        return self._chain_id

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "timestamp": hex(1_700_000_000),
        }

    def get_code(self, address: str, block: int | str = "latest") -> str:
        del block
        if address.lower() == MARKETS["BNBUSD"].address.lower():
            return self.prediction_code
        return self.oracle_code

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to, data, block
        if self.fail_historical_call:
            raise RpcError("missing trie node")
        return "0x" + "00" * 12 + "11" * 20


def test_archive_probe_verifies_historical_prediction_and_oracle_state() -> None:
    result = probe_archive_state(_ArchiveRpc(), MARKETS["BNBUSD"], 10_333_825)
    assert result.chain_id == 56
    assert result.market == "BNBUSD"
    assert result.block_number == 10_333_825
    assert result.oracle_address == "0x" + "11" * 20
    assert result.prediction_code_present is True
    assert result.oracle_code_present is True


def test_archive_probe_rejects_wrong_chain() -> None:
    with pytest.raises(RpcError, match="expected BSC"):
        probe_archive_state(_ArchiveRpc(chain_id=1), MARKETS["BNBUSD"], 10_333_825)


def test_archive_probe_fails_fast_when_historical_state_is_pruned() -> None:
    with pytest.raises(RpcError, match="archive-capable BSC RPC"):
        probe_archive_state(
            _ArchiveRpc(fail_historical_call=True),
            MARKETS["BNBUSD"],
            10_333_825,
        )


def test_archive_probe_rejects_predeployment_block() -> None:
    with pytest.raises(RpcError, match="no code"):
        probe_archive_state(
            _ArchiveRpc(prediction_code="0x"),
            MARKETS["BNBUSD"],
            10_333_824,
        )
