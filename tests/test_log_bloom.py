from __future__ import annotations

import pytest

from pancake_prediction.log_bloom import (
    BLOOM_BYTES,
    block_bloom_might_match,
    bloom_bit_indexes,
    bloom_might_contain,
)

ADDRESS = "0x" + "11" * 20
TOPIC_A = "0x" + "aa" * 32
TOPIC_B = "0x" + "bb" * 32


def _build_bloom(*values: bytes) -> str:
    bloom = 0
    for value in values:
        for index in bloom_bit_indexes(value):
            bloom |= 1 << index
    return "0x" + bloom.to_bytes(BLOOM_BYTES, "big").hex()


def test_block_bloom_matches_address_and_any_requested_topic_without_false_negative() -> None:
    bloom = _build_bloom(bytes.fromhex(ADDRESS[2:]), bytes.fromhex(TOPIC_B[2:]))

    assert bloom_might_contain(bloom, bytes.fromhex(ADDRESS[2:])) is True
    assert block_bloom_might_match(
        bloom,
        address=ADDRESS,
        topic0s=(TOPIC_A, TOPIC_B),
    ) is True
    assert block_bloom_might_match(bloom, address=ADDRESS, topic0s=None) is True


def test_block_bloom_rejects_missing_address_or_topic() -> None:
    address_only = _build_bloom(bytes.fromhex(ADDRESS[2:]))
    topic_only = _build_bloom(bytes.fromhex(TOPIC_A[2:]))

    assert block_bloom_might_match(
        address_only,
        address=ADDRESS,
        topic0s=(TOPIC_A,),
    ) is False
    assert block_bloom_might_match(
        topic_only,
        address=ADDRESS,
        topic0s=(TOPIC_A,),
    ) is False
    assert block_bloom_might_match(address_only, address=ADDRESS, topic0s=()) is False


def test_bloom_validation_fails_closed() -> None:
    with pytest.raises(ValueError, match="256 bytes"):
        bloom_might_contain("0x00", b"value")
    with pytest.raises(ValueError, match="20 bytes"):
        block_bloom_might_match("0x" + "00" * BLOOM_BYTES, address="0x12", topic0s=None)
    with pytest.raises(ValueError, match="32 bytes"):
        block_bloom_might_match(
            _build_bloom(bytes.fromhex(ADDRESS[2:])),
            address=ADDRESS,
            topic0s=("0x12",),
        )
