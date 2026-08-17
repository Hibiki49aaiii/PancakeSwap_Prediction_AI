from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.contracts import MARKETS
from pancake_prediction.historical_bootstrap import (
    resolve_collection_range,
    run_historical_bootstrap,
)
from pancake_prediction.historical_preflight import HistoricalPreflightResult
from pancake_prediction.rpc_probe import ArchiveProbeResult
from pancake_prediction.store import EventStore


class FakeBootstrapRpc:
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
        del to, data, block
        return "0x" + "00" * 12 + "11" * 20


def _preflight() -> HistoricalPreflightResult:
    market = MARKETS["BNBUSD"]
    return HistoricalPreflightResult(
        market=market.symbol,
        head_block=200,
        deployment_block=100,
        archive_probe=ArchiveProbeResult(
            chain_id=56,
            market=market.symbol,
            block_number=100,
            block_hash="0x" + "aa" * 32,
            block_timestamp=1_700_000_100,
            oracle_address="0x" + "11" * 20,
            prediction_code_present=True,
            oracle_code_present=True,
        ),
    )


def test_collection_range_defaults_to_deployment_through_confirmed_head() -> None:
    result = resolve_collection_range(_preflight(), confirmations=64)
    assert result.from_block == 100
    assert result.to_block == 136


def test_collection_range_rejects_unconfirmed_or_predeployment_blocks() -> None:
    with pytest.raises(ValueError, match="earlier than deployment"):
        resolve_collection_range(_preflight(), from_block=99)
    with pytest.raises(ValueError, match="confirmed head"):
        resolve_collection_range(_preflight(), confirmations=64, to_block=137)


def test_historical_bootstrap_initializes_db_collects_and_replays(tmp_path: Path) -> None:
    database = tmp_path / "history.sqlite3"
    result = run_historical_bootstrap(
        FakeBootstrapRpc(),
        MARKETS["BNBUSD"],
        database,
        confirmations=64,
    )
    assert database.exists()
    assert result.collection_range.from_block == 100
    assert result.collection_range.to_block == 136
    assert result.collection["prediction_events_inserted"] == 0
    assert result.quality.starts == 0
    assert result.replay_rounds == 0
    assert len(result.replay_input_digest) == 64
    assert len(result.replay_output_digest) == 64

    store = EventStore(database)
    assert store.metadata("BNBUSD.oracle_anchor_block") == "100"
    assert store.metadata("BNBUSD.oracle_anchor_address") == "0x" + "11" * 20
