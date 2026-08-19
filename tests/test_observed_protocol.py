from __future__ import annotations

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.observed_protocol import (
    latest_observed_protocol_header,
    observe_protocol_head_once,
)
from pancake_prediction_ai.onchain_collector import PinnedProtocolSnapshot
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.rpc_snapshot import BlockAnchor
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_oracle_reference, normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
ORACLE = "0x1111111111111111111111111111111111111111"


def _hash(number: int, salt: int = 0) -> str:
    return "0x" + f"{number * 100 + salt:064x}"


class FakeHeadRpc:
    def __init__(self, number: int, block_hash: str, parent_hash: str):
        self.number = number
        self.block_hash = block_hash
        self.parent_hash = parent_hash

    def chain_id(self):
        return 56

    def block_number(self):
        return self.number

    def call(self, method, params):
        assert method == "eth_getBlockByNumber"
        assert params == [hex(self.number), False]
        return {
            "number": hex(self.number),
            "hash": self.block_hash,
            "parentHash": self.parent_hash,
            "timestamp": hex(1_000 + self.number * 3),
        }


def _snapshot(number: int, block_hash: str, observed_at_ns: int) -> PinnedProtocolSnapshot:
    anchor = BlockAnchor(number, block_hash, 1_000 + number * 3)
    round_state = PredictionRoundState(
        epoch=7,
        start_timestamp=900,
        lock_timestamp=1200,
        close_timestamp=1500,
        lock_price=0,
        close_price=0,
        lock_oracle_id=0,
        close_oracle_id=0,
        total_amount_wei=300,
        bull_amount_wei=120,
        bear_amount_wei=180,
        reward_base_cal_amount_wei=0,
        reward_amount_wei=0,
        oracle_called=False,
    )
    round_event = normalize_round_snapshot(
        round_state,
        contract_address=PREDICTION,
        treasury_fee_units=300,
        block_number=number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=observed_at_ns,
    )
    oracle_ref = normalize_oracle_reference(
        ORACLE,
        contract_address=PREDICTION,
        block_number=number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=observed_at_ns,
    )
    chainlink = normalize_latest_round_data(
        (123, 600_00000000, anchor.timestamp_s - 20, anchor.timestamp_s - 10, 123),
        decimals=8,
        feed_address=ORACLE,
        observed_at_ns=observed_at_ns,
        description="BNB / USD",
    )
    return PinnedProtocolSnapshot(
        anchor=anchor,
        current_epoch=7,
        treasury_fee_units=300,
        oracle_address=ORACLE,
        oracle_decimals=8,
        oracle_description="BNB / USD",
        round_state=round_state,
        events=(round_event, oracle_ref, chainlink),
    )


def test_first_head_observation_persists_anchor_and_protocol_atomically(tmp_path, monkeypatch) -> None:
    def fake_collect(client, *, block_number, prediction_contract, clock_ns):
        return _snapshot(block_number, _hash(block_number), clock_ns())

    monkeypatch.setattr(
        "pancake_prediction_ai.observed_protocol.collect_protocol_snapshot_at_block",
        fake_collect,
    )
    client = FakeHeadRpc(10, _hash(10), _hash(9))
    with EventStore(tmp_path / "observed.sqlite") as store:
        result = observe_protocol_head_once(client, store, clock_ns=lambda: 9_000_000_000)
        assert result.status == "observed"
        assert result.anomalies == ()
        assert len(result.stored_events) == 4
        assert store.verify_chain()
        latest = latest_observed_protocol_header(store)
        assert latest is not None
        assert latest.number == 10
        assert latest.block_hash == _hash(10)


def test_same_head_is_noop_and_does_not_recollect_protocol(tmp_path, monkeypatch) -> None:
    calls = 0

    def fake_collect(client, *, block_number, prediction_contract, clock_ns):
        nonlocal calls
        calls += 1
        return _snapshot(block_number, _hash(block_number), clock_ns())

    monkeypatch.setattr(
        "pancake_prediction_ai.observed_protocol.collect_protocol_snapshot_at_block",
        fake_collect,
    )
    client = FakeHeadRpc(10, _hash(10), _hash(9))
    with EventStore(tmp_path / "observed.sqlite") as store:
        first = observe_protocol_head_once(client, store, clock_ns=lambda: 9_000_000_000)
        before = len(store.read_all_ingest_order())
        second = observe_protocol_head_once(client, store, clock_ns=lambda: 10_000_000_000)
        assert first.status == "observed"
        assert second.status == "unchanged"
        assert second.stored_events == ()
        assert len(store.read_all_ingest_order()) == before
        assert calls == 1


def test_parent_hash_mismatch_is_durable_anomaly_but_new_head_snapshot_is_kept(tmp_path, monkeypatch) -> None:
    def fake_collect(client, *, block_number, prediction_contract, clock_ns):
        return _snapshot(block_number, client.block_hash, clock_ns())

    monkeypatch.setattr(
        "pancake_prediction_ai.observed_protocol.collect_protocol_snapshot_at_block",
        fake_collect,
    )
    first = FakeHeadRpc(10, _hash(10), _hash(9))
    second = FakeHeadRpc(11, _hash(11), _hash(9, 99))  # not child of observed block 10
    clock = iter([9_000_000_000, 10_000_000_000]).__next__
    with EventStore(tmp_path / "observed.sqlite") as store:
        observe_protocol_head_once(first, store, clock_ns=clock)
        result = observe_protocol_head_once(second, store, clock_ns=clock)
        assert result.status == "observed"
        assert result.anomalies == ("parent_hash_mismatch",)
        anomaly_events = [
            item.event
            for item in store.read_all_ingest_order()
            if item.event.topic == "collector.protocol_anomaly"
        ]
        assert anomaly_events[-1].payload["anomaly"] == "parent_hash_mismatch"
        assert latest_observed_protocol_header(store).number == 11  # type: ignore[union-attr]


def test_same_height_reorg_is_audit_only_until_new_height(tmp_path, monkeypatch) -> None:
    calls = 0

    def fake_collect(client, *, block_number, prediction_contract, clock_ns):
        nonlocal calls
        calls += 1
        return _snapshot(block_number, client.block_hash, clock_ns())

    monkeypatch.setattr(
        "pancake_prediction_ai.observed_protocol.collect_protocol_snapshot_at_block",
        fake_collect,
    )
    first = FakeHeadRpc(10, _hash(10), _hash(9))
    reorged = FakeHeadRpc(10, _hash(10, 1), _hash(9, 1))
    clock = iter([9_000_000_000, 10_000_000_000]).__next__
    with EventStore(tmp_path / "observed.sqlite") as store:
        observe_protocol_head_once(first, store, clock_ns=clock)
        result = observe_protocol_head_once(reorged, store, clock_ns=clock)
        assert result.status == "reorg_or_regression"
        assert result.protocol_snapshot is None
        assert result.anomalies == ("same_height_reorg",)
        assert calls == 1
        assert any(
            item.event.payload.get("anomaly") == "same_height_reorg"
            for item in store.read_all_ingest_order()
            if item.event.topic == "collector.protocol_anomaly"
        )


def test_reconstructed_store_is_rejected(tmp_path) -> None:
    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        try:
            observe_protocol_head_once(
                FakeHeadRpc(10, _hash(10), _hash(9)),
                store,
            )
        except ValueError as exc:
            assert "observed Event Store" in str(exc)
        else:
            raise AssertionError("reconstructed store should have been rejected")
