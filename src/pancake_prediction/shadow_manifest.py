from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_FORBIDDEN_KEYS = {
    "credential",
    "credentials",
    "endpoint",
    "mnemonic",
    "password",
    "private_key",
    "secret",
    "token",
    "url",
    "username",
}


def _normalized_address(value: str, *, field: str) -> str:
    normalized = value.lower()
    if (
        not normalized.startswith("0x")
        or len(normalized) != 42
        or any(char not in "0123456789abcdef" for char in normalized[2:])
    ):
        raise ValueError(f"{field} must be a 20-byte hex address")
    return normalized


def _normalize_json(value: object, *, path: str = "semantic_config") -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise ValueError(f"{path} keys must be non-empty strings")
            if raw_key.lower() in _FORBIDDEN_KEYS:
                raise ValueError(f"{path}.{raw_key} is not allowed in campaign manifest")
            normalized[raw_key] = _normalize_json(
                raw_value,
                path=f"{path}.{raw_key}",
            )
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _normalize_json(item, path=f"{path}[]")
            for item in value
        ]
    raise ValueError(f"{path} contains unsupported value type {type(value).__name__}")


def canonical_manifest_fragment_digest(value: Mapping[str, object]) -> str:
    normalized = _normalize_json(value, path="manifest_fragment")
    raw = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256((raw + "\n").encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ShadowCampaignManifest:
    chain_id: int
    market: str
    prediction_contract: str
    oracle_proxy_anchor: str
    chainlink_aggregator_anchor: str
    semantic_config: Mapping[str, object]
    manifest_version: int = 1

    def canonical_payload(self) -> dict[str, object]:
        if self.manifest_version != 1:
            raise ValueError("unsupported shadow campaign manifest version")
        if self.chain_id <= 0:
            raise ValueError("campaign manifest chain_id must be positive")
        if not self.market:
            raise ValueError("campaign manifest market must be non-empty")
        return {
            "manifest_version": self.manifest_version,
            "chain_id": self.chain_id,
            "market": self.market,
            "prediction_contract": _normalized_address(
                self.prediction_contract,
                field="prediction_contract",
            ),
            "oracle_proxy_anchor": _normalized_address(
                self.oracle_proxy_anchor,
                field="oracle_proxy_anchor",
            ),
            "chainlink_aggregator_anchor": _normalized_address(
                self.chainlink_aggregator_anchor,
                field="chainlink_aggregator_anchor",
            ),
            "semantic_config": _normalize_json(self.semantic_config),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256((self.canonical_json() + "\n").encode()).hexdigest()
