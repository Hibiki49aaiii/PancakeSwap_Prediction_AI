from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pancake_prediction.abi import PREDICTION_EVENTS
from pancake_prediction.contracts import MARKETS
from pancake_prediction.public_collector import PublicHistoricalCollector
from pancake_prediction.recent_bootstrap import ChainlinkRouteAnchor
from pancake_prediction.rpc import RpcError
from pancake_prediction.store import EventStore
from scripts.run_recent_public_bootstrap import load_chainlink_route_anchor

ORACLE_PROXY = "0x" + "33" * 20
CHAINLINK_AGGREGATOR = "0x" + "55" * 20
ANCHOR_SHA = "ab" * 32


def _block(number: int) -> dict[str, Any]:
    return {
        "number": hex(number),
        "hash": "0x" + f"{number:064x}",
        "parentHash": "0x" + f"{max(0, number - 1):064x}",
        "timestamp": hex(number),
    }


def _change_log(*, block_number: int, topic0: str, address: str) -> dict[str, Any]:
    return {
        "address": address,
        "blockNumber": hex(block_number),
        "blockHash": _block(block_number)["hash"],
        "transactionHash": "0x" + "77" * 32,
        "transactionIndex": "0x0",
        "logIndex": "0x0",
        "topics": [topic0],
        "data": "0x",
    }


class AnchorLogRpc:
    def __init__(self, *, prediction_change: bool = False, aggregator_change: bool = False) -> None:
        self.prediction_change = prediction_change
        self.aggregator_change = aggregator_change
        self.eth_call_count = 0

    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        raise AssertionError("anchored proof must not read current head")

    def block(self, number: int) -> dict[str, Any]:
        return _block(number)

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        *,
        topic0s: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        assert (from_block, to_block) == (100, 120)
        assert topic0s is not None and len(topic0s) == 1
        if address.lower() == MARKETS["BNBUSD"].address.lower() and self.prediction_change:
            new_oracle = next(spec for spec in PREDICTION_EVENTS if spec.name == "NewOracle")
            return [
                _change_log(
                    block_number=105,
                    topic0=new_oracle.topic0,
                    address=MARKETS["BNBUSD"].address,
                )
            ]
        if address.lower() == ORACLE_PROXY and self.aggregator_change:
            return [
                _change_log(
                    block_number=110,
                    topic0=topic0s[0],
                    address=ORACLE_PROXY,
                )
            ]
        return []

    def get_code(self, address: str, block: int | str = "latest") -> str:
        del address, block
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        del to, data, block
        self.eth_call_count += 1
        raise AssertionError("anchored proof must not use historical or latest eth_call")


def _collector(tmp_path: Path, rpc: AnchorLogRpc) -> PublicHistoricalCollector:
    store = EventStore(tmp_path / "anchor.sqlite3")
    store.initialize()
    return PublicHistoricalCollector(rpc=rpc, store=store, consistency_retries=1)


def test_anchored_route_proof_uses_only_fixed_change_logs(tmp_path: Path) -> None:
    rpc = AnchorLogRpc()
    proof = _collector(tmp_path, rpc).prove_oracle_stable_from_anchor(
        MARKETS["BNBUSD"],
        from_block=100,
        through_block=115,
        anchor_block=120,
        oracle_proxy=ORACLE_PROXY,
        chainlink_aggregator=CHAINLINK_AGGREGATOR,
        anchor_evidence_sha256=ANCHOR_SHA,
    )
    assert rpc.eth_call_count == 0
    assert proof["oracle"] == ORACLE_PROXY
    assert proof["chainlink_aggregator"] == CHAINLINK_AGGREGATOR
    assert proof["through_block"] == 115
    assert proof["proof_through_block"] == 120
    assert proof["anchor_evidence_sha256"] == ANCHOR_SHA
    assert proof["historical_state_required"] is False


def test_anchored_route_proof_rejects_prediction_change(tmp_path: Path) -> None:
    collector = _collector(tmp_path, AnchorLogRpc(prediction_change=True))
    with pytest.raises(RpcError, match="NewOracle"):
        collector.prove_oracle_stable_from_anchor(
            MARKETS["BNBUSD"],
            from_block=100,
            through_block=115,
            anchor_block=120,
            oracle_proxy=ORACLE_PROXY,
            chainlink_aggregator=CHAINLINK_AGGREGATOR,
            anchor_evidence_sha256=ANCHOR_SHA,
        )


def test_anchored_route_proof_rejects_aggregator_change(tmp_path: Path) -> None:
    collector = _collector(tmp_path, AnchorLogRpc(aggregator_change=True))
    with pytest.raises(RpcError, match="AggregatorConfirmed"):
        collector.prove_oracle_stable_from_anchor(
            MARKETS["BNBUSD"],
            from_block=100,
            through_block=115,
            anchor_block=120,
            oracle_proxy=ORACLE_PROXY,
            chainlink_aggregator=CHAINLINK_AGGREGATOR,
            anchor_evidence_sha256=ANCHOR_SHA,
        )


def test_anchored_route_proof_rejects_anchor_before_window_end(tmp_path: Path) -> None:
    collector = _collector(tmp_path, AnchorLogRpc())
    with pytest.raises(ValueError, match="at or after"):
        collector.prove_oracle_stable_from_anchor(
            MARKETS["BNBUSD"],
            from_block=100,
            through_block=121,
            anchor_block=120,
            oracle_proxy=ORACLE_PROXY,
            chainlink_aggregator=CHAINLINK_AGGREGATOR,
            anchor_evidence_sha256=ANCHOR_SHA,
        )


def _anchor_payload() -> dict[str, object]:
    return {
        "success": True,
        "workflow_outcome": "success",
        "chainlink_collected": True,
        "market": "BNBUSD",
        "selected": {
            "report": {
                "authoritative_prediction_events": True,
                "chainlink_collected": True,
                "oracle_stability_proof": {
                    "oracle": ORACLE_PROXY,
                    "chainlink_aggregator": CHAINLINK_AGGREGATOR,
                    "from_block": 120,
                    "through_block": 150,
                    "new_oracle_events": 0,
                    "aggregator_confirmed_events": 0,
                },
                "collection": {
                    "oracle_addresses": [ORACLE_PROXY],
                    "chainlink_event_addresses": [CHAINLINK_AGGREGATOR],
                    "chainlink_events_inserted": 10,
                },
            }
        },
    }


def test_load_chainlink_route_anchor_binds_exact_evidence_bytes(tmp_path: Path) -> None:
    path = tmp_path / "anchor.json"
    raw = json.dumps(_anchor_payload(), indent=2, sort_keys=True).encode() + b"\n"
    path.write_bytes(raw)
    anchor = load_chainlink_route_anchor(path, market="BNBUSD")
    assert anchor == ChainlinkRouteAnchor(
        oracle_proxy=ORACLE_PROXY,
        chainlink_aggregator=CHAINLINK_AGGREGATOR,
        anchor_block=120,
        evidence_sha256=hashlib.sha256(raw).hexdigest(),
    )


def test_load_chainlink_route_anchor_fails_closed_on_route_disagreement(tmp_path: Path) -> None:
    payload = _anchor_payload()
    report = payload["selected"]["report"]  # type: ignore[index]
    report["collection"]["chainlink_event_addresses"] = ["0x" + "66" * 20]  # type: ignore[index]
    path = tmp_path / "anchor.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="aggregator disagrees"):
        load_chainlink_route_anchor(path, market="BNBUSD")
