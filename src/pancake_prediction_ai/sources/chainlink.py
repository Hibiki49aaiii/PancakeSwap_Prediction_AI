from __future__ import annotations

from typing import Sequence

from ..event_store import EventRecord


def _address(value: str) -> str:
    normalized = value.lower()
    if not normalized.startswith("0x") or len(normalized) != 42:
        raise ValueError("feed_address must be a 20-byte hex address")
    try:
        int(normalized[2:], 16)
    except ValueError as exc:
        raise ValueError("feed_address must be hex") from exc
    return normalized


def normalize_latest_round_data(
    values: Sequence[object],
    *,
    decimals: int,
    feed_address: str,
    observed_at_ns: int,
    description: str | None = None,
) -> EventRecord:
    """Normalize Chainlink AggregatorV3Interface.latestRoundData().

    Interface order is `(roundId, answer, startedAt, updatedAt, answeredInRound)`.
    Chainlink timestamps are seconds; observation time remains the independent
    local arrival timestamp supplied by the caller.
    """

    if len(values) != 5:
        raise ValueError(f"latestRoundData must contain 5 values, got {len(values)}")
    if not 0 <= decimals <= 36:
        raise ValueError("decimals must be in [0, 36]")
    if observed_at_ns < 0:
        raise ValueError("observed_at_ns must be non-negative")

    round_id = int(values[0])
    answer = int(values[1])
    started_at_s = int(values[2])
    updated_at_s = int(values[3])
    answered_in_round = int(values[4])
    if round_id <= 0:
        raise ValueError("round_id must be positive")
    if answer <= 0:
        raise ValueError("price-feed answer must be positive")
    if started_at_s < 0 or updated_at_s <= 0:
        raise ValueError("Chainlink timestamps are invalid")
    if updated_at_s < started_at_s:
        raise ValueError("updatedAt cannot precede startedAt")
    if answered_in_round < round_id:
        raise ValueError("answeredInRound cannot precede roundId")

    address = _address(feed_address)
    scale = 10**decimals
    price = answer / scale
    return EventRecord(
        event_id=f"chainlink:latest_round:{address}:{round_id}:{updated_at_s}",
        source="chainlink",
        topic="oracle.latest_round",
        event_time_ns=updated_at_s * 1_000_000_000,
        observed_at_ns=observed_at_ns,
        payload={
            "feed_address": address,
            "description": description or "",
            "round_id": round_id,
            "raw_answer": answer,
            "decimals": decimals,
            "price": price,
            "started_at_s": started_at_s,
            "updated_at_s": updated_at_s,
            "answered_in_round": answered_in_round,
        },
    )
