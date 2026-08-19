from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


# The Stage 5B trust boundary is deliberately narrow. Any change to one of
# these files changes the aggregate source fingerprint and invalidates older
# Stage 5B execution evidence at Stage 6A.
STAGE5B_SOURCE_FILES = (
    "abi_codec.py",
    "pancake_contract.py",
    "protocol_binding.py",
    "read_only_rpc.py",
    "local_fork_rpc.py",
    "fork_harness.py",
    "fork_execution.py",
    "stage5b_evidence.py",
    "readiness_cli.py",
    "evidence_gate.py",
    "stage5b_source_fingerprint.py",
)


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def capture_stage5b_source_fingerprint() -> dict[str, Any]:
    """Hash the installed source files that implement the Stage 5B trust path.

    This is a same-code binding, not a remote attestation. Stage 6A recomputes
    the manifest from its own installed source and rejects evidence generated
    against any different byte-for-byte implementation.
    """

    package_dir = Path(__file__).resolve().parent
    files: dict[str, str] = {}
    for name in STAGE5B_SOURCE_FILES:
        path = package_dir / name
        if not path.is_file():
            raise RuntimeError(f"Stage 5B source file is missing: {name}")
        files[f"pancake_prediction_ai/{name}"] = hashlib.sha256(path.read_bytes()).hexdigest()

    aggregate = hashlib.sha256(_canonical(files)).hexdigest()
    return {
        "algorithm": "sha256",
        "files": files,
        "aggregate_sha256": aggregate,
    }


def validate_stage5b_source_fingerprint_payload(payload: object) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if set(payload) != {"algorithm", "files", "aggregate_sha256"}:
        return False
    if payload.get("algorithm") != "sha256":
        return False

    files = payload.get("files")
    aggregate = payload.get("aggregate_sha256")
    if not isinstance(files, Mapping) or not _valid_sha256(aggregate):
        return False

    expected_paths = {
        f"pancake_prediction_ai/{name}" for name in STAGE5B_SOURCE_FILES
    }
    if set(files) != expected_paths:
        return False
    if any(not isinstance(path, str) or not _valid_sha256(value) for path, value in files.items()):
        return False
    return hashlib.sha256(_canonical(dict(files))).hexdigest() == aggregate


def stage5b_source_fingerprint_matches_current(payload: object) -> bool:
    if not validate_stage5b_source_fingerprint_payload(payload):
        return False
    return dict(payload) == capture_stage5b_source_fingerprint()
