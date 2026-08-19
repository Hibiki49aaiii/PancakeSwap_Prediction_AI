from __future__ import annotations

from pancake_prediction_ai.runtime_fingerprint import (
    RUNTIME_FINGERPRINT_SCHEMA,
    capture_runtime_fingerprint,
    fingerprint_sha256,
    validate_runtime_fingerprint_payload,
)


def test_runtime_fingerprint_is_canonical_stable_and_non_identifying() -> None:
    first = capture_runtime_fingerprint()
    second = capture_runtime_fingerprint()
    assert first == second
    assert first.schema == RUNTIME_FINGERPRINT_SCHEMA
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64

    payload = first.payload()
    assert validate_runtime_fingerprint_payload(payload)
    assert fingerprint_sha256(payload) == first.sha256
    assert payload["sqlite_compile_options"] == sorted(payload["sqlite_compile_options"])
    assert "hostname" not in payload
    assert "username" not in payload
    assert "machine_id" not in payload
    assert "path" not in payload


def test_runtime_fingerprint_validation_rejects_unsorted_or_duplicate_compile_options() -> None:
    payload = capture_runtime_fingerprint().payload()
    options = list(payload["sqlite_compile_options"])
    assert options

    reversed_payload = dict(payload)
    reversed_payload["sqlite_compile_options"] = list(reversed(options))
    if len(options) > 1 and options != list(reversed(options)):
        assert not validate_runtime_fingerprint_payload(reversed_payload)

    duplicate_payload = dict(payload)
    duplicate_payload["sqlite_compile_options"] = options + [options[0]]
    assert not validate_runtime_fingerprint_payload(duplicate_payload)


def test_runtime_fingerprint_hash_changes_when_runtime_component_changes() -> None:
    payload = capture_runtime_fingerprint().payload()
    changed = dict(payload)
    changed["python_version"] = str(payload["python_version"]) + "-changed"
    assert fingerprint_sha256(changed) != fingerprint_sha256(payload)
