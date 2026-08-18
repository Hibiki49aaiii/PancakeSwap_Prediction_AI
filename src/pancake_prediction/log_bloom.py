from __future__ import annotations

from .abi import keccak256

BLOOM_BYTES = 256
BLOOM_BITS = BLOOM_BYTES * 8


def _hex_bytes(value: str, *, expected_bytes: int, field: str) -> bytes:
    if not value.startswith("0x"):
        raise ValueError(f"{field} must be 0x-prefixed")
    raw = value[2:]
    if len(raw) != expected_bytes * 2:
        raise ValueError(f"{field} must be {expected_bytes} bytes")
    try:
        return bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"{field} must contain valid hex") from exc


def bloom_bit_indexes(value: bytes) -> tuple[int, int, int]:
    digest = keccak256(value)
    return tuple(((digest[offset] << 8) | digest[offset + 1]) & 2047 for offset in (0, 2, 4))


def bloom_might_contain(logs_bloom: str, value: bytes) -> bool:
    bloom = int.from_bytes(
        _hex_bytes(logs_bloom, expected_bytes=BLOOM_BYTES, field="logsBloom"),
        "big",
    )
    return all((bloom & (1 << index)) != 0 for index in bloom_bit_indexes(value))


def block_bloom_might_match(
    logs_bloom: str,
    *,
    address: str,
    topic0s: tuple[str, ...] | None,
) -> bool:
    address_bytes = _hex_bytes(address, expected_bytes=20, field="address")
    if not bloom_might_contain(logs_bloom, address_bytes):
        return False
    if topic0s is None:
        return True
    if not topic0s:
        return False
    return any(
        bloom_might_contain(
            logs_bloom,
            _hex_bytes(topic, expected_bytes=32, field="topic0"),
        )
        for topic in topic0s
    )
