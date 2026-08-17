from pathlib import Path
from typing import Any

from pancake_prediction.abi import PREDICTION_EVENTS
from pancake_prediction.collector import (
    ANALYTIC_PREDICTION_EVENT_NAMES,
    HistoricalCollector,
)
from pancake_prediction.contracts import MARKETS
from pancake_prediction.store import EventStore


class _AddressTrackingRpc:
    def __init__(self) -> None:
        self.log_addresses: list[str] = []

    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return 1_000

    def get_code(self, address: str, block: int | str = "latest") -> str:
        del address, block
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to, data, block
        return "0x" + "00" * 12 + "11" * 20

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        del from_block, to_block, topic0s
        self.log_addresses.append(address.lower())
        return []

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "parentHash": "0x" + f"{max(0, number - 1):064x}",
            "timestamp": hex(1_000 + number),
        }


def _store_canonical_new_oracle(store: EventStore, oracle: str) -> None:
    block = {
        "number": "0x4",
        "hash": "0x" + "44" * 32,
        "parentHash": "0x" + "33" * 32,
        "timestamp": "0x3ec",
    }
    store.upsert_block(56, block)
    store.insert_event(
        chain_id=56,
        contract_address=MARKETS["BNBUSD"].address,
        market="BNBUSD",
        source="prediction",
        log={
            "blockNumber": "0x4",
            "blockHash": block["hash"],
            "transactionHash": "0x" + "55" * 32,
            "transactionIndex": "0x0",
            "logIndex": "0x0",
            "topics": ["0x" + "66" * 32],
            "data": "0x",
        },
        event_name="NewOracle",
        decoded={"oracle": oracle},
    )


def test_resume_recovers_intermediate_oracle_from_canonical_store(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    historical_oracle = "0x" + "22" * 20
    current_oracle = "0x" + "11" * 20
    _store_canonical_new_oracle(store, historical_oracle)

    rpc = _AddressTrackingRpc()
    collector = HistoricalCollector(rpc, store, chunk_size=4, reorg_lookback=0)
    requested_names = set(ANALYTIC_PREDICTION_EVENT_NAMES)
    requested_names.add("NewOracle")
    topic0s = tuple(
        spec.topic0 for spec in PREDICTION_EVENTS if spec.name in requested_names
    )
    checkpoint_key = collector._checkpoint_key(
        chain_id=56,
        address=MARKETS["BNBUSD"].address,
        market="BNBUSD",
        source="prediction",
        specs=PREDICTION_EVENTS,
        from_block=1,
        topic0s=topic0s,
    )
    store.record_metadata(checkpoint_key, "8")

    report = collector.collect_market(
        MARKETS["BNBUSD"],
        1,
        8,
        include_chainlink=True,
        prediction_analytic_only=True,
    )

    assert MARKETS["BNBUSD"].address.lower() not in rpc.log_addresses
    assert historical_oracle in rpc.log_addresses
    assert current_oracle in rpc.log_addresses
    assert report["oracle_addresses"] == sorted({historical_oracle, current_oracle})
