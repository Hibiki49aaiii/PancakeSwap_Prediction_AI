from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_MASK64 = (1 << 64) - 1
_ROTATION = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)


def _rotl64(value: int, shift: int) -> int:
    if shift == 0:
        return value & _MASK64
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(state: list[int]) -> None:
    for rc in _ROUND_CONSTANTS:
        c = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(
                    state[x + 5 * y], _ROTATION[x][y]
                )

        for x in range(5):
            for y in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ (
                    ((~b[(x + 1) % 5 + 5 * y]) & _MASK64)
                    & b[(x + 2) % 5 + 5 * y]
                )

        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256, implemented locally to keep runtime dependency-free."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0)
    padded.append(0x80)

    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset : offset + rate]
        for index in range(rate // 8):
            lane = int.from_bytes(block[index * 8 : index * 8 + 8], "little")
            state[index] ^= lane
        _keccak_f1600(state)

    output = bytearray()
    while len(output) < 32:
        for index in range(rate // 8):
            output.extend(state[index].to_bytes(8, "little"))
            if len(output) >= 32:
                break
        if len(output) < 32:
            _keccak_f1600(state)
    return bytes(output[:32])


@dataclass(frozen=True, slots=True)
class EventSpec:
    name: str
    signature: str
    indexed: tuple[tuple[str, str], ...]
    data: tuple[tuple[str, str], ...]

    @property
    def topic0(self) -> str:
        return "0x" + keccak256(self.signature.encode()).hex()


PREDICTION_EVENTS: tuple[EventSpec, ...] = (
    EventSpec(
        "BetBear",
        "BetBear(address,uint256,uint256)",
        (("sender", "address"), ("epoch", "uint256")),
        (("amount", "uint256"),),
    ),
    EventSpec(
        "BetBull",
        "BetBull(address,uint256,uint256)",
        (("sender", "address"), ("epoch", "uint256")),
        (("amount", "uint256"),),
    ),
    EventSpec(
        "Claim",
        "Claim(address,uint256,uint256)",
        (("sender", "address"), ("epoch", "uint256")),
        (("amount", "uint256"),),
    ),
    EventSpec(
        "EndRound",
        "EndRound(uint256,uint256,int256)",
        (("epoch", "uint256"), ("roundId", "uint256")),
        (("price", "int256"),),
    ),
    EventSpec(
        "LockRound",
        "LockRound(uint256,uint256,int256)",
        (("epoch", "uint256"), ("roundId", "uint256")),
        (("price", "int256"),),
    ),
    EventSpec(
        "RewardsCalculated",
        "RewardsCalculated(uint256,uint256,uint256,uint256)",
        (("epoch", "uint256"),),
        (
            ("rewardBaseCalAmount", "uint256"),
            ("rewardAmount", "uint256"),
            ("treasuryAmount", "uint256"),
        ),
    ),
    EventSpec("StartRound", "StartRound(uint256)", (("epoch", "uint256"),), ()),
    EventSpec("Pause", "Pause(uint256)", (("epoch", "uint256"),), ()),
    EventSpec("Unpause", "Unpause(uint256)", (("epoch", "uint256"),), ()),
    EventSpec("NewOracle", "NewOracle(address)", (), (("oracle", "address"),)),
    EventSpec(
        "NewTreasuryFee",
        "NewTreasuryFee(uint256,uint256)",
        (("epoch", "uint256"),),
        (("treasuryFee", "uint256"),),
    ),
    EventSpec(
        "NewMinBetAmount",
        "NewMinBetAmount(uint256,uint256)",
        (("epoch", "uint256"),),
        (("minBetAmount", "uint256"),),
    ),
    EventSpec(
        "NewBufferAndIntervalSeconds",
        "NewBufferAndIntervalSeconds(uint256,uint256)",
        (),
        (("bufferSeconds", "uint256"), ("intervalSeconds", "uint256")),
    ),
)

CHAINLINK_EVENTS: tuple[EventSpec, ...] = (
    EventSpec(
        "AnswerUpdated",
        "AnswerUpdated(int256,uint256,uint256)",
        (("current", "int256"), ("roundId", "uint256")),
        (("updatedAt", "uint256"),),
    ),
)


def _decode_static(value_type: str, raw: bytes) -> object:
    if len(raw) != 32:
        raise ValueError("static ABI value must be exactly 32 bytes")
    if value_type == "address":
        return "0x" + raw[-20:].hex()
    if value_type == "uint256":
        return int.from_bytes(raw, "big", signed=False)
    if value_type == "int256":
        return int.from_bytes(raw, "big", signed=True)
    raise ValueError(f"unsupported ABI type: {value_type}")


def decode_event(
    log: dict[str, Any], specs: tuple[EventSpec, ...]
) -> tuple[str, dict[str, object]] | None:
    topics = log.get("topics")
    if not isinstance(topics, list) or not topics:
        return None
    topic0 = str(topics[0]).lower()
    spec = next((candidate for candidate in specs if candidate.topic0.lower() == topic0), None)
    if spec is None or len(topics) - 1 != len(spec.indexed):
        return None

    values: dict[str, object] = {}
    for (name, value_type), topic in zip(spec.indexed, topics[1:], strict=True):
        raw = bytes.fromhex(str(topic).removeprefix("0x"))
        values[name] = _decode_static(value_type, raw)

    data_raw = bytes.fromhex(str(log.get("data", "0x")).removeprefix("0x"))
    expected = len(spec.data) * 32
    if len(data_raw) != expected:
        return None
    for index, (name, value_type) in enumerate(spec.data):
        slot = data_raw[index * 32 : (index + 1) * 32]
        values[name] = _decode_static(value_type, slot)
    return spec.name, values


def function_selector(signature: str) -> str:
    return "0x" + keccak256(signature.encode())[:4].hex()


def decode_address_result(result: str) -> str:
    raw = bytes.fromhex(result.removeprefix("0x"))
    if len(raw) < 32:
        raise ValueError("invalid ABI address result")
    return "0x" + raw[-20:].hex()
