from __future__ import annotations

from pancake_prediction_ai.event_store import EventRecord, EventStore
from pancake_prediction_ai.historical_public_rpc import (
    PredictionBetLog,
    PredictionStaticConfig,
    backfill_public_rpc_decision_snapshots,
    collect_prediction_bet_logs,
)
from pancake_prediction_ai.pancake_events import LifecycleKind
from pancake_prediction_ai.round_history import build_round_timelines
from pancake_prediction_ai.rpc_snapshot import BlockAnchor


BLOCK_HASH = "0x" + "ab" * 32
TX_HASH = "0x" + "cd" * 32
ORACLE = "0x2222222222222222222222222222222222222222"


def _lifecycle(
    kind: LifecycleKind,
    *,
    epoch: int,
    timestamp_s: int,
    block_number: int,
    price: int | None,
    oracle_round_id: int | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=f"{kind.value}:{epoch}:{block_number}",
        source="pancake_prediction",
        topic="prediction.round_lifecycle",
        event_time_ns=timestamp_s * 1_000_000_000,
        observed_at_ns=timestamp_s * 1_000_000_000 + 1,
        payload={
            "kind": kind.value,
            "epoch": epoch,
            "price": price,
            "oracle_round_id": oracle_round_id,
            "block_number": block_number,
        },
    )


def _bet_topic_address(address: str) -> str:
    return "0x" + "00" * 12 + address[2:].lower()


def _uint_topic(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _uint_data(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


class BetRpc:
    def chain_id(self) -> int:
        return 56

    def call(self, method: str, params: list[object]) -> object:
        assert method == "eth_getLogs"
        return [
            {
                "address": "0x18b2a687610328590bc8f2e5fedde3b582a49cda",
                "topics": [
                    "0x" + __import__("eth_hash.auto", fromlist=["keccak"]).keccak(
                        b"BetBull(address,uint256,uint256)"
                    ).hex(),
                    _bet_topic_address("0x1111111111111111111111111111111111111111"),
                    _uint_topic(77),
                ],
                "data": _uint_data(123),
                "blockNumber": hex(100),
                "blockHash": BLOCK_HASH,
                "transactionHash": TX_HASH,
                "logIndex": "0x2",
                "removed": False,
            }
        ]


def test_bet_log_collection_decodes_indexed_sender_epoch_and_amount() -> None:
    result = collect_prediction_bet_logs(
        BetRpc(),  # type: ignore[arg-type]
        from_block=100,
        to_block=100,
    )
    assert len(result) == 1
    bet = result[0]
    assert bet.side == "BULL"
    assert bet.sender == "0x1111111111111111111111111111111111111111"
    assert bet.epoch == 77
    assert bet.amount_wei == 123
    assert bet.block_number == 100
    assert bet.log_index == 2


def test_public_rpc_snapshot_uses_scheduled_lock_and_excludes_post_anchor_bet(tmp_path, monkeypatch) -> None:
    epoch = 77
    start = _lifecycle(
        LifecycleKind.START,
        epoch=epoch,
        timestamp_s=1_000,
        block_number=10,
        price=None,
    )
    lock = _lifecycle(
        LifecycleKind.LOCK,
        epoch=epoch,
        timestamp_s=1_068,  # operator executes 8 seconds after the configured lock
        block_number=30,
        price=100,
        oracle_round_id=500,
    )
    end = _lifecycle(
        LifecycleKind.END,
        epoch=epoch,
        timestamp_s=1_128,
        block_number=50,
        price=101,
        oracle_round_id=501,
    )
    timeline = build_round_timelines((start, lock, end), interval_seconds=60).completed[0]
    assert timeline.lock_timestamp_ns == 1_060_000_000_000

    monkeypatch.setattr(
        "pancake_prediction_ai.historical_public_rpc.find_block_at_or_before_timestamp",
        lambda client, *, target_timestamp_s, lower_block, upper_block: BlockAnchor(
            number=20,
            block_hash=BLOCK_HASH,
            timestamp_s=1_048,
        ),
    )
    monkeypatch.setattr(
        "pancake_prediction_ai.historical_public_rpc._oracle_round_at_or_before_cutoff",
        lambda *args, **kwargs: (499, 25_000_000_000, 1_040, 1_045, 499),
    )

    bets = (
        PredictionBetLog(
            side="BULL",
            epoch=epoch,
            amount_wei=100,
            sender="0x1111111111111111111111111111111111111111",
            block_number=19,
            block_hash=BLOCK_HASH,
            transaction_hash="0x" + "01" * 32,
            log_index=0,
        ),
        PredictionBetLog(
            side="BEAR",
            epoch=epoch,
            amount_wei=200,
            sender="0x2222222222222222222222222222222222222222",
            block_number=21,  # after the decision anchor and must be excluded
            block_hash=BLOCK_HASH,
            transaction_hash="0x" + "02" * 32,
            log_index=0,
        ),
    )
    static = PredictionStaticConfig(
        head_block=100,
        interval_seconds=60,
        treasury_fee_units=300,
        oracle_address=ORACLE,
        oracle_decimals=8,
        oracle_description="BNB / USD",
        captured_at_ns=2_000_000_000_000,
    )

    with EventStore(tmp_path / "history.sqlite", mode="reconstructed") as store:
        result = backfill_public_rpc_decision_snapshots(
            object(),  # type: ignore[arg-type]
            store,
            (timeline,),
            bets,
            dataset_id="public-history-v1",
            static_config=static,
            decision_lead_ns=10_000_000_000,
            assumed_onchain_latency_ns=2_000_000_000,
        )
        assert len(result.points) == 1
        assert result.points[0].decision_cutoff_ns == 1_050_000_000_000
        events = store.read_all_ingest_order()
        round_event = next(
            item.event for item in events if item.event.topic == "prediction.round_snapshot"
        )
        assert round_event.payload["lock_timestamp"] == 1_060
        assert round_event.payload["bull_amount_wei"] == 100
        assert round_event.payload["bear_amount_wei"] == 0
        assert round_event.payload["total_amount_wei"] == 100
        reconstruction = round_event.payload["historical_reconstruction"]
        assert reconstruction["bet_log_count"] == 1
        assert len(reconstruction["bet_log_sha256"]) == 64
        assert round_event.observed_at_ns == 1_050_000_000_000

        oracle_event = next(item.event for item in events if item.event.topic == "oracle.latest_round")
        assert oracle_event.payload["round_id"] == 499
        assert oracle_event.observed_at_ns == 1_047_000_000_000
        assert store.verify_chain()
