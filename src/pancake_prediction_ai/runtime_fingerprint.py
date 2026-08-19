from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Mapping


RUNTIME_FINGERPRINT_SCHEMA = "execution_runtime_fingerprint_v1"


@dataclass(frozen=True, slots=True)
class RuntimeFingerprint:
    schema: str
    python_implementation: str
    python_version: str
    platform_system: str
    platform_release: str
    platform_machine: str
    sqlite_version: str
    sqlite_source_id: str
    sqlite_compile_options: tuple[str, ...]

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sqlite_compile_options"] = list(self.sqlite_compile_options)
        return payload

    @property
    def sha256(self) -> str:
        return fingerprint_sha256(self.payload())


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def fingerprint_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def capture_runtime_fingerprint() -> RuntimeFingerprint:
    """Capture the software/runtime stack relevant to SQLite durability behavior.

    This intentionally excludes hostnames, machine IDs, usernames and filesystem
    paths so evidence does not leak host identity. The fingerprint binds a drill
    to its Python/OS/architecture/SQLite stack, not to a unique physical host.
    """

    connection = sqlite3.connect(":memory:")
    try:
        source_id_row = connection.execute("SELECT sqlite_source_id()").fetchone()
        source_id = str(source_id_row[0]) if source_id_row else ""
        options = tuple(
            sorted(
                str(row[0])
                for row in connection.execute("PRAGMA compile_options").fetchall()
            )
        )
    finally:
        connection.close()

    return RuntimeFingerprint(
        schema=RUNTIME_FINGERPRINT_SCHEMA,
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        platform_system=platform.system(),
        platform_release=platform.release(),
        platform_machine=platform.machine(),
        sqlite_version=sqlite3.sqlite_version,
        sqlite_source_id=source_id,
        sqlite_compile_options=options,
    )


def validate_runtime_fingerprint_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("schema") != RUNTIME_FINGERPRINT_SCHEMA:
        return False
    required_strings = (
        "python_implementation",
        "python_version",
        "platform_system",
        "platform_release",
        "platform_machine",
        "sqlite_version",
        "sqlite_source_id",
    )
    if any(not isinstance(payload.get(key), str) or not payload.get(key) for key in required_strings):
        return False
    options = payload.get("sqlite_compile_options")
    if not isinstance(options, list) or not options:
        return False
    if any(not isinstance(value, str) or not value for value in options):
        return False
    if options != sorted(options) or len(options) != len(set(options)):
        return False
    return True
