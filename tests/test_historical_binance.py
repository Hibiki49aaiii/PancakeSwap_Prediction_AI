from __future__ import annotations

import pytest

from pancake_prediction_ai.binance_ingest import BinanceDataGapError
from pancake_prediction_ai.event_store import EventStore
from pancake_prediction_ai.historical_binance import backfill_binance_aggregate_trades
from pancake_prediction_ai.provenance import AvailabilityMode, availability_mode
from pancake_prediction_ai.sources.binance import normalize_rest_agg_trade


def _trade(trade_id: int, trade_time_ms: int, *, captured_at_ns: int):
    return normalize_rest_agg_trade(
        {
            "a": trade_id,
            "p": "600",
            "q": "1",
            "f": trade_id,
            "l": trade_id,
            "T": trade_time_ms,
            "m": False,
        },
        symbol="BNBUSDT",
        observed_at_ns=captured_at_ns,
    )


class FakeClient:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def collect_aggregate_trades(self, symbol: str, *, from_id=None, start_time_ms=None, end_time_ms=None, limit=500):
        self.calls.append((from_id, start_time_ms, end_time_ms, limit))
        return tuple(self.batches.pop(0)) if self.batches else ()


def test_historical_backfill_uses_assumed_availability_and_separate_store(tmp_path) -> None:
    client = FakeClient(
        [
            [_trade(10, 1000, captured_at_ns=99_000), _trade(11, 1100, captured_at_ns=99_000)],
            [_trade(12, 1200, captured_at_ns=100_000), _trade(13, 2100, captured_at_ns=100_000)],
        ]
    )
    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        result = backfill_binance_aggregate_trades(
            client,
            store,
            dataset_id="bnb-history-v1",
            start_time_ms=1000,
            end_time_ms=2000,
            assumed_latency_ns=5_000_000,
            batch_limit=2,
        )
        assert result.events_appended == 3
        assert result.first_aggregate_trade_id == 10
        assert result.last_aggregate_trade_id == 12
        events = store.read_all_ingest_order()
        assert len(events) == 3
        assert all(availability_mode(item.event) is AvailabilityMode.RECONSTRUCTED for item in events)
        assert events[0].event.observed_at_ns == 1_000_000_000 + 5_000_000
        metadata = events[0].event.payload["_availability_provenance"]
        assert metadata["captured_at_ns"] == 99_000
        assert metadata["dataset_id"] == "bnb-history-v1"
        assert store.verify_chain()

    assert client.calls[0] == (None, 1000, None, 2)
    assert client.calls[1] == (12, None, None, 2)


def test_historical_backfill_refuses_observed_store(tmp_path) -> None:
    with EventStore(tmp_path / "observed.sqlite") as store:
        with pytest.raises(ValueError, match="reconstructed Event Store"):
            backfill_binance_aggregate_trades(
                FakeClient([]),
                store,
                dataset_id="x",
                start_time_ms=1,
                end_time_ms=2,
                assumed_latency_ns=0,
            )


def test_historical_sequence_gap_is_hard_failure(tmp_path) -> None:
    client = FakeClient(
        [[_trade(10, 1000, captured_at_ns=1), _trade(12, 1100, captured_at_ns=1)]]
    )
    with EventStore(tmp_path / "historical.sqlite", mode="reconstructed") as store:
        with pytest.raises(BinanceDataGapError, match="expected 11, received 12"):
            backfill_binance_aggregate_trades(
                client,
                store,
                dataset_id="x",
                start_time_ms=1000,
                end_time_ms=2000,
                assumed_latency_ns=0,
                batch_limit=2,
            )
        # Gap occurs before the page is committed, so the page remains atomic.
        assert store.read_all_ingest_order() == ()
