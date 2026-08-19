from __future__ import annotations

from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.historical_onchain import (
    backfill_protocol_at_timestamps,
    reconstruct_protocol_snapshot,
)
from pancake_prediction_ai.onchain_collector import PinnedProtocolSnapshot
from pancake_prediction_ai.pancake_contract import PredictionRoundState
from pancake_prediction_ai.provenance import AvailabilityMode, availability_mode
from pancake_prediction_ai.rpc_snapshot import BlockAnchor
from pancake_prediction_ai.sources.chainlink import normalize_latest_round_data
from pancake_prediction_ai.sources.pancake import normalize_oracle_reference, normalize_round_snapshot


PREDICTION = "0x18b2a687610328590bc8f2e5fedde3b582a49cda"
ORACLE = "0x1111111111111111111111111111111111111111"


def _snapshot(anchor: BlockAnchor, captured_at_ns: int) -> PinnedProtocolSnapshot:
    round_state = PredictionRoundState(
        epoch=anchor.number,
        start_timestamp=anchor.timestamp_s - 100,
        lock_timestamp=anchor.timestamp_s + 100,
        close_timestamp=anchor.timestamp_s + 400,
        lock_price=600_00000000,
        close_price=0,
        lock_oracle_id=10,
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
        block_number=anchor.number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=captured_at_ns,
    )
    oracle_ref = normalize_oracle_reference(
        ORACLE,
        contract_address=PREDICTION,
        block_number=anchor.number,
        block_timestamp_s=anchor.timestamp_s,
        observed_at_ns=captured_at_ns,
    )
    chainlink = normalize_latest_round_data(
        (123, 600_00000000, anchor.timestamp_s - 20, anchor.timestamp_s - 10, 123),
        decimals=8,
        feed_address=ORACLE,
        observed_at_ns=captured_at_ns,
        description="BNB / USD",
    )
    return PinnedProtocolSnapshot(
        anchor=anchor,
        current_epoch=anchor.number,
        treasury_fee_units=300,
        oracle_address=ORACLE,
        oracle_decimals=8,
        oracle_description="BNB / USD",
        round_state=round_state,
        events=(round_event, oracle_ref, chainlink),
    )


def test_chainlink_source_time_is_preserved_but_reconstructed_availability_uses_block_time() -> None:
    anchor = BlockAnchor(100, "0x" + "ab" * 32, 1_000)
    source = _snapshot(anchor, captured_at_ns=9_000_000_000_000)
    reconstructed = reconstruct_protocol_snapshot(
        source,
        dataset_id="protocol-history-v1",
        assumed_latency_ns=2_000_000_000,
    )
    assert len(reconstructed) == 3
    assert all(availability_mode(event) is AvailabilityMode.RECONSTRUCTED for event in reconstructed)
    assert {event.observed_at_ns for event in reconstructed} == {1_002_000_000_000}

    chainlink = reconstructed[2]
    assert chainlink.event_time_ns == 990_000_000_000
    metadata = chainlink.payload["_availability_provenance"]
    assert metadata["availability_basis"] == "block_timestamp"
    assert metadata["availability_base_ns"] == 1_000_000_000_000
    assert metadata["captured_at_ns"] == 9_000_000_000_000


class FakeHistoryRpc:
    def chain_id(self) -> int:
        return 56

    def call(self, method: str, params: list[object]):
        assert method == "eth_getBlockByNumber"
        number = int(str(params[0]), 16)
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "timestamp": hex(1_000 + number * 10),
        }


def test_timestamp_backfill_deduplicates_same_anchor_and_commits_reconstructed_batches(
    tmp_path, monkeypatch
) -> None:
    captured = iter([9_000_000_000_000, 9_100_000_000_000]).__next__

    def fake_collect(client, *, anchor, prediction_contract, clock_ns):
        return _snapshot(anchor, captured_at_ns=clock_ns())

    monkeypatch.setattr(
        "pancake_prediction_ai.historical_onchain.collect_protocol_snapshot_at_anchor",
        fake_collect,
    )

    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        result = backfill_protocol_at_timestamps(
            FakeHistoryRpc(),  # type: ignore[arg-type]
            store,
            dataset_id="protocol-history-v1",
            target_timestamps_s=(1_055, 1_059, 1_075),
            lower_block=0,
            upper_block=10,
            assumed_latency_ns=1_000_000_000,
            clock_ns=captured,
        )
        # 1055 and 1059 both resolve to block 5 (timestamp 1050); 1075 -> block 7.
        assert [point.anchor.number for point in result.points] == [5, 7]
        assert result.duplicate_anchor_targets_skipped == 1
        assert result.events_appended == 6
        assert len(store.read_all_ingest_order()) == 6
        assert store.verify_chain()
        assert all(
            availability_mode(item.event) is AvailabilityMode.RECONSTRUCTED
            for item in store.read_all_ingest_order()
        )
