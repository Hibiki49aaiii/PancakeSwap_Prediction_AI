from __future__ import annotations

from dataclasses import dataclass

from .binance_ingest import BinanceDataGapError
from .binance_public_rest import BinancePublicRestClient
from .event_store import EventRecord, EventStore
from .provenance import ReconstructionPolicy, reconstruct_event


@dataclass(frozen=True, slots=True)
class HistoricalBinanceBackfillResult:
    dataset_id: str
    events_appended: int
    first_aggregate_trade_id: int | None
    last_aggregate_trade_id: int | None
    first_trade_time_ms: int | None
    last_trade_time_ms: int | None


def _trade_id(event: EventRecord) -> int:
    value = event.payload.get("aggregate_trade_id")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("aggregate_trade_id must be integer")
    return value


def _trade_time_ms(event: EventRecord) -> int:
    value = event.payload.get("trade_time_ms")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("trade_time_ms must be integer")
    return value


def backfill_binance_aggregate_trades(
    client: BinancePublicRestClient,
    store: EventStore,
    *,
    dataset_id: str,
    symbol: str = "BNBUSDT",
    start_time_ms: int,
    end_time_ms: int,
    assumed_latency_ns: int,
    batch_limit: int = 1000,
    max_batches: int = 10_000,
) -> HistoricalBinanceBackfillResult:
    """Build an assumption-labeled historical aggTrade dataset.

    The first REST page is anchored by `startTime`; continuation pages use the
    last aggregate trade ID + 1. The API response-arrival time remains recorded
    in reconstruction metadata, while replay availability is explicitly set to
    `trade_time + assumed_latency`.

    The requested interval is safe to replay after interruption: deterministic
    reconstructed event IDs already present in the store are skipped rather than
    inserted again. Sequence validation still runs over every fetched page, so an
    idempotent retry cannot hide a Binance aggregate-trade gap.
    """

    if store.mode != "reconstructed":
        raise ValueError("historical backfill requires reconstructed Event Store")
    if not dataset_id:
        raise ValueError("dataset_id is required")
    if start_time_ms < 0 or end_time_ms < 0 or start_time_ms >= end_time_ms:
        raise ValueError("historical time range is invalid")
    if assumed_latency_ns < 0:
        raise ValueError("assumed_latency_ns must be non-negative")
    if not 1 <= batch_limit <= 1000:
        raise ValueError("batch_limit must be in [1, 1000]")
    if max_batches <= 0:
        raise ValueError("max_batches must be positive")

    existing_event_ids = {
        stored.event.event_id for stored in store.read_all_ingest_order()
    }
    appended = 0
    first_id: int | None = None
    last_id: int | None = None
    first_time: int | None = None
    last_time: int | None = None
    next_from_id: int | None = None

    for _batch_index in range(max_batches):
        if next_from_id is None:
            rows = client.collect_aggregate_trades(
                symbol,
                start_time_ms=start_time_ms,
                limit=batch_limit,
            )
        else:
            rows = client.collect_aggregate_trades(
                symbol,
                from_id=next_from_id,
                limit=batch_limit,
            )
        if not rows:
            return HistoricalBinanceBackfillResult(
                dataset_id, appended, first_id, last_id, first_time, last_time
            )

        reconstructed_batch: list[EventRecord] = []
        reached_end = False
        for row in rows:
            trade_id = _trade_id(row)
            trade_time = _trade_time_ms(row)
            if last_id is not None:
                expected = last_id + 1
                if trade_id != expected:
                    raise BinanceDataGapError(
                        f"historical aggregate trade gap: expected {expected}, received {trade_id}"
                    )
            if trade_time < start_time_ms:
                raise ValueError("Binance returned trade before requested historical start")
            if trade_time > end_time_ms:
                reached_end = True
                break

            policy = ReconstructionPolicy(
                dataset_id=dataset_id,
                assumed_latency_ns=assumed_latency_ns,
                captured_at_ns=row.observed_at_ns,
            )
            reconstructed = reconstruct_event(row, policy=policy)
            if reconstructed.event_id not in existing_event_ids:
                reconstructed_batch.append(reconstructed)
                existing_event_ids.add(reconstructed.event_id)
            if first_id is None:
                first_id = trade_id
                first_time = trade_time
            last_id = trade_id
            last_time = trade_time

        if reconstructed_batch:
            store.append_many(reconstructed_batch)
            appended += len(reconstructed_batch)

        if reached_end or len(rows) < batch_limit:
            return HistoricalBinanceBackfillResult(
                dataset_id, appended, first_id, last_id, first_time, last_time
            )
        if last_id is None:
            raise BinanceDataGapError("historical page contained no in-range trade to continue from")
        next_from_id = last_id + 1

    raise BinanceDataGapError(
        f"historical backfill exceeded max_batches={max_batches} before reaching end_time_ms"
    )
