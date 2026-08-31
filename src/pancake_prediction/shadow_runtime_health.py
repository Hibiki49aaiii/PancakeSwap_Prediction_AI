from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

_RUNTIME_STATUSES = frozenset(
    {
        "cycle_success",
        "cycle_error_retry",
        "cycle_error_fatal",
    }
)


@dataclass(frozen=True)
class ShadowRuntimeHealthReport:
    check_passed: bool
    operationally_alive: bool
    degraded: bool
    reason: str
    runtime_status: str | None
    fresh: bool
    age_ms: int | None
    last_success_at_ms: int | None
    last_success_age_ms: int | None
    consecutive_cycle_errors: int | None
    signing_enabled: bool = False
    live_broadcast: bool = False
    funded_execution: bool = False
    profitability_gate_eligible: bool = False
    campaign_evidence_checked: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _invalid_report(reason: str) -> ShadowRuntimeHealthReport:
    return ShadowRuntimeHealthReport(
        check_passed=False,
        operationally_alive=False,
        degraded=True,
        reason=reason,
        runtime_status=None,
        fresh=False,
        age_ms=None,
        last_success_at_ms=None,
        last_success_age_ms=None,
        consecutive_cycle_errors=None,
    )


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _positive_seconds_to_ms(value: float, *, label: str) -> int:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return max(1, math.ceil(value * 1_000))


def _load_status_payload(path: Path) -> dict[str, object] | ShadowRuntimeHealthReport:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return _invalid_report("status_file_unreadable")
    except json.JSONDecodeError:
        return _invalid_report("status_json_invalid")
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        return _invalid_report("status_schema_invalid")
    return cast(dict[str, object], raw)


def _validate_status_payload(
    payload: dict[str, object],
) -> tuple[str, int, int | None, int] | None:
    runtime_status = payload.get("status")
    if not isinstance(runtime_status, str) or runtime_status not in _RUNTIME_STATUSES:
        return None

    updated_at_ms = _nonnegative_int(payload.get("updated_at_ms"))
    consecutive_cycle_errors = _nonnegative_int(payload.get("consecutive_cycle_errors"))
    if updated_at_ms is None or consecutive_cycle_errors is None:
        return None

    last_success_raw = payload.get("last_success_at_ms")
    last_success_at_ms = (
        None if last_success_raw is None else _nonnegative_int(last_success_raw)
    )
    if last_success_raw is not None and last_success_at_ms is None:
        return None
    if last_success_at_ms is not None and last_success_at_ms > updated_at_ms:
        return None

    for key in (
        "signing_enabled",
        "live_broadcast",
        "funded_execution",
        "profitability_gate_eligible",
    ):
        if payload.get(key) is not False:
            return None

    if runtime_status == "cycle_success":
        cycle_status = payload.get("cycle_status")
        if (
            not isinstance(cycle_status, str)
            or not cycle_status.strip()
            or consecutive_cycle_errors != 0
            or last_success_at_ms != updated_at_ms
        ):
            return None
        return (
            runtime_status,
            updated_at_ms,
            last_success_at_ms,
            consecutive_cycle_errors,
        )

    error_type = payload.get("error_type")
    max_errors = _nonnegative_int(payload.get("max_consecutive_cycle_errors"))
    if (
        not isinstance(error_type, str)
        or not error_type.strip()
        or max_errors is None
        or max_errors < 1
        or consecutive_cycle_errors < 1
    ):
        return None

    if runtime_status == "cycle_error_retry":
        if consecutive_cycle_errors >= max_errors:
            return None
        if _positive_finite_number(payload.get("retry_after_seconds")) is None:
            return None

    return (
        runtime_status,
        updated_at_ms,
        last_success_at_ms,
        consecutive_cycle_errors,
    )


def inspect_shadow_runtime_health(
    status_file: Path,
    *,
    max_status_age_seconds: float,
    max_last_success_age_seconds: float | None = None,
    now_ms: int | None = None,
) -> ShadowRuntimeHealthReport:
    max_status_age_ms = _positive_seconds_to_ms(
        max_status_age_seconds,
        label="max_status_age_seconds",
    )
    max_last_success_age_ms = (
        None
        if max_last_success_age_seconds is None
        else _positive_seconds_to_ms(
            max_last_success_age_seconds,
            label="max_last_success_age_seconds",
        )
    )

    loaded = _load_status_payload(status_file)
    if isinstance(loaded, ShadowRuntimeHealthReport):
        return loaded

    validated = _validate_status_payload(loaded)
    if validated is None:
        return _invalid_report("status_schema_invalid")

    runtime_status, updated_at_ms, last_success_at_ms, consecutive_cycle_errors = validated

    effective_now_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if (
        isinstance(effective_now_ms, bool)
        or not isinstance(effective_now_ms, int)
        or effective_now_ms < 0
    ):
        raise ValueError("now_ms must be a non-negative integer")
    if updated_at_ms > effective_now_ms:
        return _invalid_report("status_timestamp_in_future")

    age_ms = effective_now_ms - updated_at_ms
    last_success_age_ms = (
        None
        if last_success_at_ms is None
        else effective_now_ms - last_success_at_ms
    )
    fresh = age_ms <= max_status_age_ms

    if runtime_status == "cycle_error_fatal":
        return ShadowRuntimeHealthReport(
            check_passed=False,
            operationally_alive=False,
            degraded=True,
            reason="fatal_status",
            runtime_status=runtime_status,
            fresh=fresh,
            age_ms=age_ms,
            last_success_at_ms=last_success_at_ms,
            last_success_age_ms=last_success_age_ms,
            consecutive_cycle_errors=consecutive_cycle_errors,
        )

    if not fresh:
        return ShadowRuntimeHealthReport(
            check_passed=False,
            operationally_alive=False,
            degraded=True,
            reason="stale_status",
            runtime_status=runtime_status,
            fresh=False,
            age_ms=age_ms,
            last_success_at_ms=last_success_at_ms,
            last_success_age_ms=last_success_age_ms,
            consecutive_cycle_errors=consecutive_cycle_errors,
        )

    if max_last_success_age_ms is not None:
        if last_success_age_ms is None:
            return ShadowRuntimeHealthReport(
                check_passed=False,
                operationally_alive=True,
                degraded=True,
                reason="last_success_missing",
                runtime_status=runtime_status,
                fresh=True,
                age_ms=age_ms,
                last_success_at_ms=None,
                last_success_age_ms=None,
                consecutive_cycle_errors=consecutive_cycle_errors,
            )
        if last_success_age_ms > max_last_success_age_ms:
            return ShadowRuntimeHealthReport(
                check_passed=False,
                operationally_alive=True,
                degraded=True,
                reason="last_success_stale",
                runtime_status=runtime_status,
                fresh=True,
                age_ms=age_ms,
                last_success_at_ms=last_success_at_ms,
                last_success_age_ms=last_success_age_ms,
                consecutive_cycle_errors=consecutive_cycle_errors,
            )

    is_retry = runtime_status == "cycle_error_retry"
    return ShadowRuntimeHealthReport(
        check_passed=True,
        operationally_alive=True,
        degraded=is_retry,
        reason="fresh_retry" if is_retry else "fresh_success",
        runtime_status=runtime_status,
        fresh=True,
        age_ms=age_ms,
        last_success_at_ms=last_success_at_ms,
        last_success_age_ms=last_success_age_ms,
        consecutive_cycle_errors=consecutive_cycle_errors,
    )
