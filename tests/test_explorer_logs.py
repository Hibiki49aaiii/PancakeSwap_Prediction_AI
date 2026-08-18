from __future__ import annotations

import json
import urllib.error
from collections.abc import Iterator
from typing import Any

import pytest

from pancake_prediction.collector import HistoricalCollector
from pancake_prediction.explorer_logs import (
    EtherscanV2LogsClient,
    ExplorerApiError,
    HybridExplorerRpc,
    normalize_explorer_log,
)
from pancake_prediction.rpc import RpcError
from pancake_prediction.store import EventStore

ADDRESS = "0x" + "11" * 20
TOPIC_A = "0x" + "aa" * 32
TOPIC_B = "0x" + "bb" * 32
BLOCK_HASH = "0x" + "cc" * 32
OTHER_BLOCK_HASH = "0x" + "dd" * 32


def _raw_log(
    *,
    topic0: str = TOPIC_A,
    block_hash: str = BLOCK_HASH,
    tx_index: int = 0,
    log_index: int = 0,
) -> dict[str, Any]:
    return {
        "address": ADDRESS,
        "topics": [topic0],
        "data": "0x",
        "blockNumber": "0x64",
        "blockHash": block_hash,
        "transactionHash": "0x" + f"{log_index + 1:064x}",
        "transactionIndex": hex(tx_index),
        "logIndex": hex(log_index),
    }


class FakeEtherscan(EtherscanV2LogsClient):
    def __init__(self, pages: dict[tuple[str | None, int], list[dict[str, Any]]]) -> None:
        super().__init__("secret-value", page_size=2, retries=1)
        self.pages = pages
        self.requested: list[tuple[str | None, int]] = []

    def _request_page(self, params: dict[str, str]) -> list[dict[str, Any]]:
        topic = params.get("topic0")
        page = int(params["page"])
        self.requested.append((topic, page))
        return self.pages.get((topic, page), [])


class CanonicalRpcFixture:
    def __init__(self, *, block_hash: str = BLOCK_HASH, chain_id: int = 56) -> None:
        self._block_hash = block_hash
        self._chain_id = chain_id

    def chain_id(self) -> int:
        return self._chain_id

    def block_number(self) -> int:
        return 100

    def block(self, number: int) -> dict[str, Any]:
        assert number == 100
        return {
            "number": "0x64",
            "hash": self._block_hash,
            "parentHash": "0x" + "22" * 32,
            "timestamp": "0x1",
        }

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        return "0x" + "00" * 32


def test_etherscan_paginates_full_pages_and_deduplicates_across_topics() -> None:
    duplicate = _raw_log(topic0=TOPIC_A, log_index=0)
    second = _raw_log(topic0=TOPIC_A, tx_index=1, log_index=1)
    other = _raw_log(topic0=TOPIC_B, tx_index=2, log_index=2)
    client = FakeEtherscan(
        {
            (TOPIC_A, 1): [duplicate, second],
            (TOPIC_A, 2): [],
            (TOPIC_B, 1): [other],
        }
    )

    logs = client.get_logs(ADDRESS, 100, 100, topic0s=(TOPIC_A, TOPIC_B))

    assert len(logs) == 3
    assert client.requested == [(TOPIC_A, 1), (TOPIC_A, 2), (TOPIC_B, 1)]
    evidence = client.evidence[-1]
    assert evidence.pages_requested == 3
    assert evidence.records_received == 3
    assert evidence.unique_records == 3
    assert len(evidence.digest) == 64


def test_etherscan_evidence_and_repr_never_contain_api_key() -> None:
    client = FakeEtherscan({(TOPIC_A, 1): []})
    client.get_logs(ADDRESS, 1, 2, topic0s=(TOPIC_A,))

    rendered = repr(client) + json.dumps(client.evidence_manifest(), sort_keys=True)
    assert "secret-value" not in rendered
    assert client.evidence_manifest()["credential_persisted"] is False


def test_normalizer_rejects_wrong_address_topic_and_missing_canonical_fields() -> None:
    with pytest.raises(ExplorerApiError, match="unexpected address"):
        normalize_explorer_log(
            {**_raw_log(), "address": "0x" + "44" * 20},
            expected_address=ADDRESS,
            expected_topic0=TOPIC_A,
        )
    with pytest.raises(ExplorerApiError, match="unexpected topic0"):
        normalize_explorer_log(
            _raw_log(topic0=TOPIC_B),
            expected_address=ADDRESS,
            expected_topic0=TOPIC_A,
        )
    malformed = _raw_log()
    malformed.pop("blockHash")
    with pytest.raises(ExplorerApiError, match="blockHash"):
        normalize_explorer_log(
            malformed,
            expected_address=ADDRESS,
            expected_topic0=TOPIC_A,
        )


def test_hybrid_explorer_rpc_rejects_chain_mismatch() -> None:
    explorer = FakeEtherscan({})
    hybrid = HybridExplorerRpc(CanonicalRpcFixture(chain_id=1), explorer)
    with pytest.raises(RpcError, match="chain id"):
        hybrid.chain_id()


def test_existing_collector_rejects_explorer_block_hash_mismatch(tmp_path) -> None:
    explorer = FakeEtherscan({(TOPIC_A, 1): [_raw_log(block_hash=OTHER_BLOCK_HASH)]})
    hybrid = HybridExplorerRpc(CanonicalRpcFixture(), explorer)
    store = EventStore(tmp_path / "events.sqlite3")
    store.initialize()
    collector = HistoricalCollector(rpc=hybrid, store=store, consistency_retries=1)

    with pytest.raises(RpcError, match="canonical block/log mismatch"):
        collector._fetch_consistent_chunk(
            address=ADDRESS,
            start=100,
            end=100,
            topic0s=(TOPIC_A,),
        )


def test_transport_error_does_not_leak_api_key(monkeypatch) -> None:
    key = "top-secret-api-key"
    client = EtherscanV2LogsClient(key, retries=1)

    def fail(*args: object, **kwargs: object) -> Iterator[bytes]:
        del args, kwargs
        raise urllib.error.URLError("network failure")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(ExplorerApiError) as caught:
        client.get_logs(ADDRESS, 100, 100, topic0s=(TOPIC_A,))
    assert key not in str(caught.value)
