from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from eth_abi import decode, encode
from eth_hash.auto import keccak


def function_selector(signature: str) -> bytes:
    if not signature or "(" not in signature or not signature.endswith(")"):
        raise ValueError("invalid Solidity function signature")
    return keccak(signature.encode("ascii"))[:4]


def encode_call(
    signature: str,
    *,
    argument_types: Sequence[str] = (),
    arguments: Sequence[Any] = (),
) -> str:
    if len(argument_types) != len(arguments):
        raise ValueError("argument type/value length mismatch")
    selector = function_selector(signature)
    encoded_arguments = encode(list(argument_types), list(arguments)) if argument_types else b""
    return "0x" + (selector + encoded_arguments).hex()


def decode_result(data: str, output_types: Sequence[str]) -> tuple[Any, ...]:
    if not isinstance(data, str) or not data.startswith("0x"):
        raise ValueError("ABI result must be hex data")
    try:
        raw = bytes.fromhex(data[2:])
    except ValueError as exc:
        raise ValueError("ABI result contains invalid hex") from exc
    if not output_types:
        if raw:
            raise ValueError("unexpected ABI output data")
        return ()
    try:
        return tuple(decode(list(output_types), raw))
    except Exception as exc:  # eth_abi exposes several decode-specific exception types
        raise ValueError("ABI result could not be decoded") from exc
