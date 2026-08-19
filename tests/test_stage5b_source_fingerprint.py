from __future__ import annotations

import hashlib
import json

from pancake_prediction_ai.stage5b_source_fingerprint import (
    STAGE5B_SOURCE_FILES,
    capture_stage5b_source_fingerprint,
    stage5b_source_fingerprint_matches_current,
    validate_stage5b_source_fingerprint_payload,
)


def test_stage5b_source_fingerprint_is_complete_and_self_consistent() -> None:
    fingerprint = capture_stage5b_source_fingerprint()
    files = fingerprint["files"]
    assert fingerprint["algorithm"] == "sha256"
    assert set(files) == {
        f"pancake_prediction_ai/{name}" for name in STAGE5B_SOURCE_FILES
    }
    assert all(len(value) == 64 for value in files.values())
    expected = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert fingerprint["aggregate_sha256"] == expected
    assert validate_stage5b_source_fingerprint_payload(fingerprint)
    assert stage5b_source_fingerprint_matches_current(fingerprint)


def test_stage5b_source_fingerprint_rejects_unknown_or_missing_files() -> None:
    fingerprint = capture_stage5b_source_fingerprint()
    files = dict(fingerprint["files"])
    files.pop(next(iter(files)))
    tampered = {
        "algorithm": "sha256",
        "files": files,
        "aggregate_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    assert not validate_stage5b_source_fingerprint_payload(tampered)
    assert not stage5b_source_fingerprint_matches_current(tampered)


def test_well_formed_but_different_source_manifest_does_not_match_current() -> None:
    fingerprint = capture_stage5b_source_fingerprint()
    files = dict(fingerprint["files"])
    first = next(iter(files))
    files[first] = "0" * 64
    tampered = {
        "algorithm": "sha256",
        "files": files,
        "aggregate_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    assert validate_stage5b_source_fingerprint_payload(tampered)
    assert not stage5b_source_fingerprint_matches_current(tampered)
