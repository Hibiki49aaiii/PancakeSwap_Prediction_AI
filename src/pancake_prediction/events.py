from __future__ import annotations

from dataclasses import dataclass

from .abi import PREDICTION_EVENT_TOPICS
from .snapshot import SnapshotLog


_TOPIC_TO_EVENT = {topic: name for name, topic in PREDICTION_EVENT_TOPICS.items()}


class EventDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PredictionEvent:
    name: str
    block_number: int
    tx_hash: str
    tx_index: int
    log_index: int
    epoch: int
    sender: str | None = None
    amount_wei: int | None = None
    oracle_round_id: int | None = None
    price: int | None = None
    reward_base_cal_amount: int | None = None
    reward_amount: int | None = None
    treasury_amount: int | None = None


def _word(value: str, *, name: str) -> bytes:
    if not value.startswith("0x"):
        raise EventDecodeError(f"{name} must be 0x-prefixed")
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError as exc:
        raise EventDecodeError(f"{name} is not hex") from exc
    if len(raw) != 32:
        raise EventDecodeError(f"{name} must be exactly 32 bytes")
    return raw


def _uint(value: str, *, name: str) -> int:
    return int.from_bytes(_word(value, name=name), "big", signed=False)


def _int(value: str, *, name: str) -> int:
    return int.from_bytes(_word(value, name=name), "big", signed=True)


def _address_topic(value: str) -> str:
    raw = _word(value, name="indexed address")
    if any(raw[:12]):
        raise EventDecodeError("indexed address is not left-padded with zeroes")
    return "0x" + raw[12:].hex()


def _data_words(data: str, count: int) -> tuple[str, ...]:
    if not data.startswith("0x"):
        raise EventDecodeError("event data must be 0x-prefixed")
    payload = data[2:]
    if len(payload) != count * 64:
        raise EventDecodeError(f"event data must contain exactly {count} ABI words")
    try:
        bytes.fromhex(payload)
    except ValueError as exc:
        raise EventDecodeError("event data is not hex") from exc
    return tuple("0x" + payload[index : index + 64] for index in range(0, len(payload), 64))


def decode_prediction_log(log: SnapshotLog) -> PredictionEvent | None:
    if log.removed:
        raise EventDecodeError("removed log cannot be replayed as canonical evidence")
    if not log.topics:
        return None
    event_name = _TOPIC_TO_EVENT.get(log.topics[0].lower())
    if event_name is None:
        return None

    base = {
        "name": event_name,
        "block_number": log.block_number,
        "tx_hash": log.tx_hash,
        "tx_index": log.tx_index,
        "log_index": log.log_index,
    }
    if event_name == "StartRound":
        if len(log.topics) != 2 or log.data != "0x":
            raise EventDecodeError("malformed StartRound log")
        return PredictionEvent(**base, epoch=_uint(log.topics[1], name="epoch"))

    if event_name in {"LockRound", "EndRound"}:
        if len(log.topics) != 3:
            raise EventDecodeError(f"malformed {event_name} topics")
        words = _data_words(log.data, 1)
        return PredictionEvent(
            **base,
            epoch=_uint(log.topics[1], name="epoch"),
            oracle_round_id=_uint(log.topics[2], name="oracle round id"),
            price=_int(words[0], name="price"),
        )

    if event_name in {"BetBull", "BetBear"}:
        if len(log.topics) != 3:
            raise EventDecodeError(f"malformed {event_name} topics")
        words = _data_words(log.data, 1)
        return PredictionEvent(
            **base,
            epoch=_uint(log.topics[2], name="epoch"),
            sender=_address_topic(log.topics[1]),
            amount_wei=_uint(words[0], name="bet amount"),
        )

    if event_name == "RewardsCalculated":
        if len(log.topics) != 2:
            raise EventDecodeError("malformed RewardsCalculated topics")
        words = _data_words(log.data, 3)
        return PredictionEvent(
            **base,
            epoch=_uint(log.topics[1], name="epoch"),
            reward_base_cal_amount=_uint(words[0], name="reward base"),
            reward_amount=_uint(words[1], name="reward amount"),
            treasury_amount=_uint(words[2], name="treasury amount"),
        )

    raise AssertionError(f"unhandled known event {event_name}")
