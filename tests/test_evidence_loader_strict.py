from __future__ import annotations

import hashlib
import json

import pytest

from pancake_prediction_ai.evidence_gate import Evidence


def _document(**changes: object) -> bytes:
    payload = {"schema": "fixture"}
    document: dict[str, object] = {
        "kind": "stage5a_drill",
        "origin": "observed",
        "passed": True,
        "recorded_at": "2026-08-19T22:35:00+09:00",
        "artifact_sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "payload": payload,
    }
    document.update(changes)
    return json.dumps(document).encode()


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, [], {}])
def test_passed_must_be_actual_json_boolean(value: object) -> None:
    with pytest.raises(ValueError, match="passed must be a boolean"):
        Evidence.from_json_bytes(_document(passed=value))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", 1, "kind must be a string"),
        ("origin", True, "origin must be a string"),
        ("recorded_at", "", "recorded_at must be a non-empty string"),
        ("artifact_sha256", 123, "artifact_sha256 must be a string"),
    ],
)
def test_top_level_evidence_types_are_strict(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Evidence.from_json_bytes(_document(**{field: value}))
