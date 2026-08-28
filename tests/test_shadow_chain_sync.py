from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pancake_prediction import shadow_chain_sync
from pancake_prediction.contracts import MARKETS
from pancake_prediction.rpc import RpcError
from pancake_prediction.store import EventStore


class FakeRpc:
    def __init__(self, head: int = 1_100):
        self.head = head

    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return self.head

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "parentHash": "0x" + f"{max(0, number - 1):064x}",
            "timestamp": hex(1_700_000_000 + number),
        }

    def get_logs(
        self,
        *,
        address: str,
        from_block: int,
        to_block: int,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        return "0x" + "00" * 32


class FakeCollector:
    instances: list[FakeCollector] = []
    proof_proxy = "0x" + "11" * 20
    proof_aggregator = "0x" + "22" * 20

    def __init__(
        self,
        *,
        rpc: object,
        store: EventStore,
        chunk_size: int,
        reorg_lookback: int,
    ):
        self.rpc = rpc
        self.store = store
        self.chunk_size = chunk_size
        self.reorg_lookback = reorg_lookback
        self.proof_calls: list[tuple[int, int]] = []
        self.market_calls: list[tuple[int, int]] = []
        self.chainlink_calls: list[tuple[str, int, int]] = []
        type(self).instances.append(self)

    def prove_latest_oracle_stable_since(
        self,
        market: object,
        *,
        from_block: int,
        through_block: int,
    ) -> dict[str, object]:
        self.proof_calls.append((from_block, through_block))
        return {
            "oracle": type(self).proof_proxy,
            "chainlink_aggregator": type(self).proof_aggregator,
            "from_block": from_block,
            "through_block": through_block,
        }

    def collect_market(
        self,
        market: object,
        from_block: int,
        to_block: int,
        *,
        include_chainlink: bool,
        prediction_analytic_only: bool,
    ) -> dict[str, object]:
        assert include_chainlink is False
        assert prediction_analytic_only is False
        self.market_calls.append((from_block, to_block))
        self.store.record_metadata("BNBUSD.last_collected_block", str(to_block))
        return {
            "prediction_events_inserted": 7,
            "reorg_blocks_detected": [from_block + 1],
        }

    def collect_chainlink_feed(
        self,
        market: object,
        *,
        aggregator_address: str,
        from_block: int,
        to_block: int,
    ) -> dict[str, object]:
        self.chainlink_calls.append((aggregator_address, from_block, to_block))
        return {"chainlink_events_inserted": 3}


def _database(tmp_path: Path, *, last_block: int = 1_000) -> Path:
    database = tmp_path / "canonical.sqlite3"
    store = EventStore(database)
    store.initialize()
    store.record_metadata("BNBUSD.last_collected_block", str(last_block))
    store.record_metadata(
        "BNBUSD.oracle_proxy_anchor_address",
        FakeCollector.proof_proxy,
    )
    store.record_metadata(
        "BNBUSD.oracle_anchor_address",
        FakeCollector.proof_aggregator,
    )
    return database


def test_shadow_chain_sync_collects_overlap_through_safe_head(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeCollector.instances.clear()
    monkeypatch.setattr(
        shadow_chain_sync,
        "PublicHistoricalCollector",
        FakeCollector,
    )
    database = _database(tmp_path)

    report = shadow_chain_sync.sync_shadow_chain(
        FakeRpc(head=1_100),
        MARKETS["BNBUSD"],
        database,
        confirmations=3,
        chunk_size=500,
        reorg_lookback=64,
    )

    assert report.previous_last_collected_block == 1_000
    assert report.safe_head_block == 1_097
    assert report.from_block == 937
    assert report.to_block == 1_097
    assert report.prediction_events_inserted == 7
    assert report.chainlink_events_inserted == 3
    assert report.reorg_blocks_detected == (938,)
    assert report.no_new_confirmed_blocks is False
    collector = FakeCollector.instances[-1]
    assert collector.proof_calls == [(937, 1_097)]
    assert collector.market_calls == [(937, 1_097)]
    assert collector.chainlink_calls == [
        (FakeCollector.proof_aggregator, 937, 1_097)
    ]


def test_shadow_chain_sync_noops_when_safe_head_not_advanced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeCollector.instances.clear()
    monkeypatch.setattr(
        shadow_chain_sync,
        "PublicHistoricalCollector",
        FakeCollector,
    )
    database = _database(tmp_path, last_block=1_000)

    report = shadow_chain_sync.sync_shadow_chain(
        FakeRpc(head=1_002),
        MARKETS["BNBUSD"],
        database,
        confirmations=3,
        reorg_lookback=64,
    )

    assert report.safe_head_block == 999
    assert report.no_new_confirmed_blocks is True
    assert report.from_block is None
    assert report.to_block is None
    assert report.prediction_events_inserted == 0
    assert report.chainlink_events_inserted == 0
    collector = FakeCollector.instances[-1]
    assert collector.proof_calls == [(937, 1_000)]
    assert collector.market_calls == []
    assert collector.chainlink_calls == []


def test_shadow_chain_sync_rejects_route_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    FakeCollector.instances.clear()
    database = _database(tmp_path)
    original = FakeCollector.proof_aggregator
    FakeCollector.proof_aggregator = "0x" + "33" * 20
    monkeypatch.setattr(
        shadow_chain_sync,
        "PublicHistoricalCollector",
        FakeCollector,
    )
    try:
        with pytest.raises(RpcError, match="source-bound campaign"):
            shadow_chain_sync.sync_shadow_chain(
                FakeRpc(),
                MARKETS["BNBUSD"],
                database,
            )
    finally:
        FakeCollector.proof_aggregator = original


def test_shadow_chain_sync_requires_existing_route_anchor(tmp_path: Path) -> None:
    database = tmp_path / "canonical.sqlite3"
    store = EventStore(database)
    store.initialize()
    store.record_metadata("BNBUSD.last_collected_block", "1000")

    with pytest.raises(ValueError, match="oracle_anchor_address"):
        shadow_chain_sync.sync_shadow_chain(
            FakeRpc(),
            MARKETS["BNBUSD"],
            database,
        )


def test_shadow_chain_sync_validates_collection_settings(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(ValueError, match="confirmations"):
        shadow_chain_sync.sync_shadow_chain(
            FakeRpc(),
            MARKETS["BNBUSD"],
            database,
            confirmations=-1,
        )
    with pytest.raises(ValueError, match="reorg_lookback"):
        shadow_chain_sync.sync_shadow_chain(
            FakeRpc(),
            MARKETS["BNBUSD"],
            database,
            reorg_lookback=0,
        )
