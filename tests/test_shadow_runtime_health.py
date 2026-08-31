import json
from pathlib import Path

import pytest

from pancake_prediction.shadow_runtime_health import inspect_shadow_runtime_health


def _success_payload(updated_at_ms: int) -> dict[str, object]:
    return {
        "status": "cycle_success",
        "cycle_status": "prediction_recorded",
        "updated_at_ms": updated_at_ms,
        "last_success_at_ms": updated_at_ms,
        "consecutive_cycle_errors": 0,
        "signing_enabled": False,
        "live_broadcast": False,
        "funded_execution": False,
        "profitability_gate_eligible": False,
    }


def _retry_payload(
    updated_at_ms: int,
    *,
    last_success_at_ms: int | None = None,
) -> dict[str, object]:
    return {
        "status": "cycle_error_retry",
        "error_type": "RpcError",
        "updated_at_ms": updated_at_ms,
        "last_success_at_ms": last_success_at_ms,
        "consecutive_cycle_errors": 1,
        "max_consecutive_cycle_errors": 5,
        "retry_after_seconds": 1.0,
        "signing_enabled": False,
        "live_broadcast": False,
        "funded_execution": False,
        "profitability_gate_eligible": False,
    }


def _fatal_payload(updated_at_ms: int) -> dict[str, object]:
    return {
        "status": "cycle_error_fatal",
        "error_type": "ValueError",
        "updated_at_ms": updated_at_ms,
        "last_success_at_ms": None,
        "consecutive_cycle_errors": 1,
        "max_consecutive_cycle_errors": 5,
        "signing_enabled": False,
        "live_broadcast": False,
        "funded_execution": False,
        "profitability_gate_eligible": False,
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_shadow_runtime_health_fresh_success_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    _write(path, _success_payload(10_000))
    before = path.read_bytes()

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=10_500,
    )

    assert report.check_passed is True
    assert report.operationally_alive is True
    assert report.degraded is False
    assert report.reason == "fresh_success"
    assert report.runtime_status == "cycle_success"
    assert report.fresh is True
    assert report.age_ms == 500
    assert report.last_success_age_ms == 500
    assert report.campaign_evidence_checked is False
    assert path.read_bytes() == before


def test_shadow_runtime_health_fresh_retry_without_success_is_alive(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    _write(path, _retry_payload(20_000))

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=20_500,
    )

    assert report.check_passed is True
    assert report.operationally_alive is True
    assert report.degraded is True
    assert report.reason == "fresh_retry"
    assert report.last_success_at_ms is None


def test_shadow_runtime_health_last_success_policy_can_fail_fresh_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "status.json"
    _write(path, _retry_payload(30_000, last_success_at_ms=20_000))

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        max_last_success_age_seconds=5.0,
        now_ms=30_500,
    )

    assert report.check_passed is False
    assert report.operationally_alive is True
    assert report.degraded is True
    assert report.reason == "last_success_stale"
    assert report.last_success_age_ms == 10_500


def test_shadow_runtime_health_last_success_policy_rejects_no_success(
    tmp_path: Path,
) -> None:
    path = tmp_path / "status.json"
    _write(path, _retry_payload(40_000))

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        max_last_success_age_seconds=10.0,
        now_ms=40_500,
    )

    assert report.check_passed is False
    assert report.operationally_alive is True
    assert report.reason == "last_success_missing"


def test_shadow_runtime_health_fatal_is_not_alive(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    _write(path, _fatal_payload(50_000))

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=50_500,
    )

    assert report.check_passed is False
    assert report.operationally_alive is False
    assert report.degraded is True
    assert report.reason == "fatal_status"
    assert report.runtime_status == "cycle_error_fatal"


def test_shadow_runtime_health_stale_status_fails(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    _write(path, _success_payload(60_000))

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=1.0,
        now_ms=62_000,
    )

    assert report.check_passed is False
    assert report.operationally_alive is False
    assert report.fresh is False
    assert report.reason == "stale_status"


def test_shadow_runtime_health_future_timestamp_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    _write(path, _success_payload(71_000))

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=70_000,
    )

    assert report.check_passed is False
    assert report.reason == "status_timestamp_in_future"
    assert report.runtime_status is None


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("{not-json", "status_json_invalid"),
        ("[]", "status_schema_invalid"),
    ],
)
def test_shadow_runtime_health_invalid_json_is_redacted(
    tmp_path: Path,
    content: str,
    reason: str,
) -> None:
    path = tmp_path / "status.json"
    path.write_text(content, encoding="utf-8")

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=80_000,
    )

    assert report.check_passed is False
    assert report.reason == reason
    assert "not-json" not in json.dumps(report.as_dict())


def test_shadow_runtime_health_missing_or_unreadable_file_is_redacted(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "secret-name-status.json"
    report = inspect_shadow_runtime_health(
        missing,
        max_status_age_seconds=2.0,
        now_ms=90_000,
    )

    rendered = json.dumps(report.as_dict())
    assert report.reason == "status_file_unreadable"
    assert "secret-name-status" not in rendered


def test_shadow_runtime_health_rejects_safety_contradiction(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    payload = _success_payload(100_000)
    payload["signing_enabled"] = True
    _write(path, payload)

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=100_500,
    )

    assert report.check_passed is False
    assert report.reason == "status_schema_invalid"


def test_shadow_runtime_health_allows_unknown_extra_fields(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    payload = _success_payload(110_000)
    payload["future_optional_field"] = {"version": 2}
    _write(path, payload)

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=110_500,
    )

    assert report.check_passed is True
    assert report.reason == "fresh_success"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "cycle_success",
            "cycle_status": "",
            "updated_at_ms": 120_000,
            "last_success_at_ms": 120_000,
            "consecutive_cycle_errors": 0,
            "signing_enabled": False,
            "live_broadcast": False,
            "funded_execution": False,
            "profitability_gate_eligible": False,
        },
        {
            "status": "cycle_error_retry",
            "error_type": "RpcError",
            "updated_at_ms": 120_000,
            "last_success_at_ms": None,
            "consecutive_cycle_errors": 5,
            "max_consecutive_cycle_errors": 5,
            "retry_after_seconds": 1.0,
            "signing_enabled": False,
            "live_broadcast": False,
            "funded_execution": False,
            "profitability_gate_eligible": False,
        },
    ],
)
def test_shadow_runtime_health_rejects_schema_invariants(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    path = tmp_path / "status.json"
    _write(path, payload)

    report = inspect_shadow_runtime_health(
        path,
        max_status_age_seconds=2.0,
        now_ms=120_500,
    )

    assert report.check_passed is False
    assert report.reason == "status_schema_invalid"


def test_shadow_runtime_health_rejects_invalid_threshold(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_status_age_seconds"):
        inspect_shadow_runtime_health(
            tmp_path / "status.json",
            max_status_age_seconds=0,
            now_ms=1,
        )
