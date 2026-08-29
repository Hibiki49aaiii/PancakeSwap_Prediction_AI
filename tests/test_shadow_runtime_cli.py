from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pancake_prediction import shadow_runtime_cli
from pancake_prediction.binance_live import (
    BinanceLiveError,
    BinanceLiveSourceIntegrityError,
)
from pancake_prediction.binance_live_lock import (
    BinanceLiveLineageLockError,
    BinanceLiveLineageProcessLock,
)
from pancake_prediction.clickhouse import ClickHouseError
from pancake_prediction.contracts import Market
from pancake_prediction.rpc import RpcError
from pancake_prediction.shadow_chain_sync import ShadowChainSourceIntegrityError
from pancake_prediction.shadow_runtime import ShadowRuntimeConfig
from pancake_prediction.shadow_runtime_lock import (
    ShadowRuntimeLockError,
    ShadowRuntimeProcessLock,
)


@dataclass(frozen=True, slots=True)
class FakeClickHouseClient:
    endpoint: str = "http://127.0.0.1:8123"
    database: str = "default"


@dataclass(frozen=True)
class FakeAudit:
    event_count: int = 12
    head_digest: str = "a" * 64
    campaign_manifest_digest: str = "c" * 64


@dataclass(frozen=True)
class FakeCampaign:
    gate_ready: bool = False
    audit: FakeAudit = field(default_factory=FakeAudit)

    @property
    def campaign_digest(self) -> str:
        return "b" * 64

    def as_dict(self) -> dict[str, object]:
        return {
            "gate_ready": self.gate_ready,
            "campaign_digest": self.campaign_digest,
            "audit": {
                "event_count": self.audit.event_count,
                "head_digest": self.audit.head_digest,
            },
            "profitability_gate_eligible": False,
            "full_historical_gate_satisfied": False,
            "signing_enabled": False,
            "live_broadcast": False,
        }


@dataclass(frozen=True)
class FakePreflightReport:
    ready: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "market": "BNBUSD",
            "ready": self.ready,
            "checks": {"structural": self.ready},
            "failures": [] if self.ready else ["structural"],
            "signing_enabled": False,
            "live_broadcast": False,
            "funded_execution": False,
            "profitability_gate_eligible": False,
        }


@dataclass(frozen=True)
class FakeCycleReport:
    status: str = "no_eligible_target"
    campaign: FakeCampaign = field(default_factory=FakeCampaign)

    def as_dict(self) -> dict[str, object]:
        return {
            "market": "BNBUSD",
            "status": self.status,
            "signing_enabled": False,
            "live_broadcast": False,
            "funded_execution": False,
            "profitability_gate_eligible": False,
        }


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--market",
        "BNBUSD",
        "--canonical-db",
        str(tmp_path / "canonical.sqlite3"),
        "--shadow-db",
        str(tmp_path / "shadow.sqlite3"),
        "--stake-wei",
        "10000000000000000",
        "--bet-gas-wei",
        "50000000000000",
        "--claim-gas-wei",
        "30000000000000",
        "--inclusion-latency-seconds",
        "2",
    ]


def test_shadow_runtime_cli_once_binds_config_and_writes_atomic_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "BSC_RPC_URL",
        "https://secret-rpc.example.invalid/private-key",
    )
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "clickhouse-secret")

    rpc = object()
    clickhouse = FakeClickHouseClient()
    binance = object()
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: rpc)
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: clickhouse,
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: binance,
    )

    captured: dict[str, object] = {}

    def fake_cycle(
        received_rpc: object,
        received_clickhouse: object,
        received_binance: object,
        market: Market,
        canonical_database: Path,
        shadow_database: Path,
        *,
        config: ShadowRuntimeConfig,
    ) -> FakeCycleReport:
        assert received_rpc is rpc
        assert received_clickhouse is clickhouse
        assert received_binance is binance
        captured.update(
            {
                "market": market.symbol,
                "canonical_database": canonical_database,
                "shadow_database": shadow_database,
                "config": config,
            }
        )
        competing_lock = ShadowRuntimeProcessLock(shadow_database)
        with pytest.raises(
            ShadowRuntimeLockError,
            match="already holds",
        ):
            competing_lock.acquire()
        assert isinstance(received_clickhouse, FakeClickHouseClient)
        competing_spot = BinanceLiveLineageProcessLock(
            received_clickhouse,
            market=market.symbol,
            venue="spot",
            timestamp_unit=config.spot_timestamp_unit,
            availability_lag_ms=config.spot_availability_lag_ms,
        )
        with pytest.raises(
            BinanceLiveLineageLockError,
            match="already writes",
        ):
            competing_spot.acquire()

        competing_perp = BinanceLiveLineageProcessLock(
            received_clickhouse,
            market=market.symbol,
            venue="um_futures",
            timestamp_unit=config.perp_timestamp_unit,
            availability_lag_ms=config.perp_availability_lag_ms,
        )
        with pytest.raises(
            BinanceLiveLineageLockError,
            match="already writes",
        ):
            competing_perp.acquire()
        shadow_database.write_bytes(b"shadow-ledger-snapshot")
        return FakeCycleReport()

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        fake_cycle,
    )

    evidence = tmp_path / "evidence" / "runtime-latest.json"
    campaign_evidence = tmp_path / "evidence" / "campaign-latest.json"
    campaign_last_success = tmp_path / "evidence" / "campaign-last-success.json"
    campaign_last_success.parent.mkdir(parents=True, exist_ok=True)
    campaign_last_success.write_text("established-success\n", encoding="utf-8")
    args = [
        *_base_args(tmp_path),
        "--once",
        "--evidence-output",
        str(evidence),
        "--campaign-evidence-output",
        str(campaign_evidence),
        "--campaign-last-success-output",
        str(campaign_last_success),
        "--chain-confirmations",
        "5",
        "--spot-timestamp-unit",
        "auto",
        "--spot-availability-lag-ms",
        "275",
        "--perp-timestamp-unit",
        "milliseconds",
        "--perp-availability-lag-ms",
        "325",
        "--binance-bootstrap-window-ms",
        "90000",
        "--decision-lead-seconds",
        "25",
        "--purge-rounds",
        "3",
        "--min-train-rounds",
        "400",
        "--calibration-rounds",
        "80",
        "--pool-min-train-rounds",
        "180",
        "--pool-window-rounds",
        "450",
    ]
    assert shadow_runtime_cli.main(args) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    stored = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload == stored
    assert payload["status"] == "no_eligible_target"
    assert payload["signing_enabled"] is False
    assert payload["live_broadcast"] is False
    assert payload["funded_execution"] is False
    assert not evidence.with_name(evidence.name + ".tmp").exists()

    campaign_payload = json.loads(campaign_evidence.read_text(encoding="utf-8"))
    assert campaign_payload["evidence_role"] == "latest_attempt"
    assert campaign_payload["success"] is False
    assert campaign_payload["workflow_outcome"] == "incomplete"
    assert campaign_payload["ledger_binding"]["campaign_digest"] == "b" * 64
    assert campaign_payload["signing_enabled"] is False
    assert campaign_payload["live_broadcast"] is False
    assert campaign_payload["funded_execution"] is False
    assert campaign_last_success.read_text(encoding="utf-8") == "established-success\n"
    assert not campaign_evidence.with_name(campaign_evidence.name + ".tmp").exists()

    config = captured["config"]
    assert isinstance(config, ShadowRuntimeConfig)
    assert config.chain_confirmations == 5
    assert config.spot_timestamp_unit == "auto"
    assert config.spot_availability_lag_ms == 275
    assert config.perp_timestamp_unit == "milliseconds"
    assert config.perp_availability_lag_ms == 325
    assert config.binance_bootstrap_window_ms == 90_000
    assert config.inference.decision_lead_seconds == 25
    assert config.inference.purge_rounds == 3
    assert config.inference.min_train_rounds == 400
    assert config.inference.calibration_rounds == 80
    assert config.inference.pool_min_train_rounds == 180
    assert config.inference.pool_window_rounds == 450

    assert "secret-rpc" not in output
    assert "clickhouse-secret" not in output
    assert "secret-rpc" not in evidence.read_text(encoding="utf-8")
    assert "clickhouse-secret" not in evidence.read_text(encoding="utf-8")
    assert "secret-rpc" not in campaign_evidence.read_text(encoding="utf-8")
    assert "clickhouse-secret" not in campaign_evidence.read_text(encoding="utf-8")


def test_shadow_runtime_cli_writes_last_success_only_for_ready_campaign(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BSC_RPC_URL", "https://secret-rpc.example.invalid/key")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: object())
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClickHouseClient(),
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: object(),
    )

    def fake_cycle(
        received_rpc: object,
        received_clickhouse: object,
        received_binance: object,
        market: Market,
        canonical_database: Path,
        shadow_database: Path,
        *,
        config: ShadowRuntimeConfig,
    ) -> FakeCycleReport:
        del received_rpc, received_clickhouse, received_binance
        del market, canonical_database, config
        shadow_database.write_bytes(b"ready-shadow-ledger")
        return FakeCycleReport(campaign=FakeCampaign(gate_ready=True))

    monkeypatch.setattr(shadow_runtime_cli, "run_shadow_runtime_cycle", fake_cycle)

    latest = tmp_path / "evidence" / "campaign-latest.json"
    last_success = tmp_path / "evidence" / "campaign-last-success.json"
    assert (
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--once",
                "--campaign-evidence-output",
                str(latest),
                "--campaign-last-success-output",
                str(last_success),
            ]
        )
        == 0
    )

    latest_payload = json.loads(latest.read_text(encoding="utf-8"))
    success_payload = json.loads(last_success.read_text(encoding="utf-8"))
    assert latest_payload["evidence_role"] == "latest_attempt"
    assert success_payload["evidence_role"] == "last_success"
    assert latest_payload["success"] is True
    assert success_payload["success"] is True
    assert (
        latest_payload["ledger_binding"]["campaign_digest"]
        == success_payload["ledger_binding"]["campaign_digest"]
        == "b" * 64
    )
    assert not latest.with_name(latest.name + ".tmp").exists()
    assert not last_success.with_name(last_success.name + ".tmp").exists()


def test_shadow_runtime_cli_rejects_conflicting_evidence_paths(tmp_path: Path) -> None:
    shared = tmp_path / "evidence.json"
    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--once",
                "--evidence-output",
                str(shared),
                "--campaign-evidence-output",
                str(shared),
            ]
        )
    assert exc_info.value.code == 2


def test_shadow_runtime_cli_lock_contention_prevents_runtime_cycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BSC_RPC_URL", "https://example.invalid")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: object())
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: object(),
    )

    def must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("runtime cycle must not run while campaign lock is held")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        must_not_run,
    )

    shadow = tmp_path / "shadow.sqlite3"
    with ShadowRuntimeProcessLock(shadow):
        with pytest.raises(SystemExit) as exc_info:
            shadow_runtime_cli.main([*_base_args(tmp_path), "--once"])
        assert exc_info.value.code == 2


def test_shadow_runtime_cli_binance_lineage_contention_prevents_runtime_cycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BSC_RPC_URL", "https://example.invalid")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    client = FakeClickHouseClient()
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: object())
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: object(),
    )

    def must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("runtime cycle must not run while lineage lock is held")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        must_not_run,
    )

    with BinanceLiveLineageProcessLock(
        client,
        market="BNBUSD",
        venue="spot",
        timestamp_unit="auto",
        availability_lag_ms=250,
    ):
        with pytest.raises(SystemExit) as exc_info:
            shadow_runtime_cli.main([*_base_args(tmp_path), "--once"])
        assert exc_info.value.code == 2


def test_shadow_runtime_cli_no_perp_acquires_only_spot_lineage_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BSC_RPC_URL", "https://example.invalid")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: object())
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: FakeClickHouseClient(),
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: object(),
    )
    observed_venues: list[str] = []

    class RecordingLineageLock:
        def __init__(
            self,
            client: object,
            *,
            market: str,
            venue: str,
            timestamp_unit: str,
            availability_lag_ms: int,
        ) -> None:
            del client, market, timestamp_unit, availability_lag_ms
            observed_venues.append(venue)

        def __enter__(self) -> RecordingLineageLock:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc_value: object,
            traceback: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinanceLiveLineageProcessLock",
        RecordingLineageLock,
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        lambda *args, **kwargs: FakeCycleReport(),
    )

    assert (
        shadow_runtime_cli.main(
            [*_base_args(tmp_path), "--once", "--no-perp"]
        )
        == 0
    )
    assert observed_venues == ["spot"]


def _patch_runtime_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> FakeClickHouseClient:
    monkeypatch.setenv("BSC_RPC_URL", "https://example.invalid")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    clickhouse = FakeClickHouseClient()
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: object())
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: clickhouse,
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: object(),
    )
    return clickhouse


def test_shadow_runtime_cli_once_cycle_error_exits_without_retry_or_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _patch_runtime_clients(monkeypatch)

    def fail_cycle(*args: object, **kwargs: object) -> object:
        raise BinanceLiveError("https://secret-binance.invalid/token")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        fail_cycle,
    )
    monkeypatch.setattr(
        "pancake_prediction.shadow_runtime_cli.time.sleep",
        lambda seconds: pytest.fail(f"once mode must not retry: {seconds}"),
    )

    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main([*_base_args(tmp_path), "--once"])
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "BinanceLiveError" in captured.err
    assert "secret-binance" not in captured.err
    assert "token" not in captured.err


@pytest.mark.parametrize(
    "error",
    (
        ValueError("sensitive-value-detail"),
        ShadowChainSourceIntegrityError("sensitive-route-detail"),
        BinanceLiveSourceIntegrityError("sensitive-lineage-detail"),
    ),
)
def test_shadow_runtime_cli_continuous_fatal_cycle_error_exits_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    error: Exception,
) -> None:
    _patch_runtime_clients(monkeypatch)

    def fail_cycle(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        fail_cycle,
    )
    monkeypatch.setattr(
        "pancake_prediction.shadow_runtime_cli.time.sleep",
        lambda seconds: pytest.fail(f"fatal cycle error must not retry: {seconds}"),
    )

    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(_base_args(tmp_path))
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert type(error).__name__ in captured.err
    assert str(error) not in captured.err


def test_shadow_runtime_cli_continuous_retries_generic_binance_live_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _patch_runtime_clients(monkeypatch)
    calls = 0

    def cycle(*args: object, **kwargs: object) -> FakeCycleReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise BinanceLiveError("sensitive-catch-up-detail")
        return FakeCycleReport()

    sleeps = 0

    def fake_sleep(seconds: float) -> None:
        nonlocal sleeps
        assert seconds == 1.0
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(shadow_runtime_cli, "run_shadow_runtime_cycle", cycle)
    monkeypatch.setattr(
        "pancake_prediction.shadow_runtime_cli.time.sleep",
        fake_sleep,
    )

    assert shadow_runtime_cli.main(_base_args(tmp_path)) == 0
    assert calls == 2
    assert sleeps == 2

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["status"] == "cycle_error_retry"
    assert lines[0]["error_type"] == "BinanceLiveError"
    assert lines[0]["consecutive_cycle_errors"] == 1
    assert lines[1]["status"] == "no_eligible_target"
    assert "sensitive-catch-up-detail" not in json.dumps(lines)


def test_shadow_runtime_cli_continuous_recovers_after_cycle_error_with_locks_held(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    clickhouse = _patch_runtime_clients(monkeypatch)
    shadow_database = tmp_path / "shadow.sqlite3"
    calls = 0

    def cycle(
        received_rpc: object,
        received_clickhouse: object,
        received_binance: object,
        market: Market,
        canonical_database: Path,
        received_shadow_database: Path,
        *,
        config: ShadowRuntimeConfig,
    ) -> FakeCycleReport:
        nonlocal calls
        del received_rpc, received_binance, canonical_database
        calls += 1
        assert received_clickhouse is clickhouse
        assert received_shadow_database == shadow_database

        competing_campaign = ShadowRuntimeProcessLock(shadow_database)
        with pytest.raises(ShadowRuntimeLockError, match="already holds"):
            competing_campaign.acquire()

        competing_spot = BinanceLiveLineageProcessLock(
            clickhouse,
            market=market.symbol,
            venue="spot",
            timestamp_unit=config.spot_timestamp_unit,
            availability_lag_ms=config.spot_availability_lag_ms,
        )
        with pytest.raises(
            BinanceLiveLineageLockError,
            match="already writes",
        ):
            competing_spot.acquire()

        if calls == 1:
            raise RpcError("https://secret-rpc.invalid/api-key")
        return FakeCycleReport()

    sleeps = 0

    def fake_sleep(seconds: float) -> None:
        nonlocal sleeps
        assert seconds == 1.0
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(shadow_runtime_cli, "run_shadow_runtime_cycle", cycle)
    monkeypatch.setattr("pancake_prediction.shadow_runtime_cli.time.sleep", fake_sleep)

    assert shadow_runtime_cli.main(_base_args(tmp_path)) == 0
    assert calls == 2
    assert sleeps == 2

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0] == {
        "consecutive_cycle_errors": 1,
        "error_type": "RpcError",
        "funded_execution": False,
        "live_broadcast": False,
        "max_consecutive_cycle_errors": 5,
        "profitability_gate_eligible": False,
        "retry_after_seconds": 1.0,
        "signing_enabled": False,
        "status": "cycle_error_retry",
    }
    assert lines[1]["status"] == "no_eligible_target"
    assert "secret-rpc" not in json.dumps(lines)


def test_shadow_runtime_cli_success_resets_consecutive_error_counter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _patch_runtime_clients(monkeypatch)
    calls = 0

    def cycle(*args: object, **kwargs: object) -> FakeCycleReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RpcError("first-secret")
        if calls == 3:
            raise ClickHouseError("second-secret")
        return FakeCycleReport()

    sleeps = 0

    def fake_sleep(seconds: float) -> None:
        nonlocal sleeps
        assert seconds == 1.0
        sleeps += 1
        if sleeps == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(shadow_runtime_cli, "run_shadow_runtime_cycle", cycle)
    monkeypatch.setattr("pancake_prediction.shadow_runtime_cli.time.sleep", fake_sleep)

    assert shadow_runtime_cli.main(_base_args(tmp_path)) == 0
    assert calls == 3

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    retries = [item for item in lines if item["status"] == "cycle_error_retry"]
    assert [item["consecutive_cycle_errors"] for item in retries] == [1, 1]
    assert [item["error_type"] for item in retries] == ["RpcError", "ClickHouseError"]
    rendered = json.dumps(lines)
    assert "first-secret" not in rendered
    assert "second-secret" not in rendered


def test_shadow_runtime_cli_exits_at_max_consecutive_cycle_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _patch_runtime_clients(monkeypatch)
    calls = 0

    def fail_cycle(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise ClickHouseError("clickhouse-sensitive-detail")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        fail_cycle,
    )
    monkeypatch.setattr("pancake_prediction.shadow_runtime_cli.time.sleep", lambda seconds: None)

    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--max-consecutive-cycle-errors",
                "3",
            ]
        )
    assert exc_info.value.code == 2
    assert calls == 3

    captured = capsys.readouterr()
    retries = [json.loads(line) for line in captured.out.splitlines()]
    assert [item["consecutive_cycle_errors"] for item in retries] == [1, 2]
    assert all(item["error_type"] == "ClickHouseError" for item in retries)
    assert "maximum consecutive cycle errors (3)" in captured.err
    assert "ClickHouseError" in captured.err
    assert "sensitive-detail" not in captured.err
    assert "sensitive-detail" not in captured.out


def test_shadow_runtime_cli_keyboard_interrupt_during_retry_sleep_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _patch_runtime_clients(monkeypatch)

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        lambda *args, **kwargs: (_ for _ in ()).throw(RpcError("hidden-detail")),
    )

    def interrupted_sleep(seconds: float) -> None:
        assert seconds == 1.0
        raise KeyboardInterrupt

    monkeypatch.setattr("pancake_prediction.shadow_runtime_cli.time.sleep", interrupted_sleep)

    assert shadow_runtime_cli.main(_base_args(tmp_path)) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "cycle_error_retry"
    assert payload["error_type"] == "RpcError"
    assert "hidden-detail" not in output


def test_shadow_runtime_cli_validates_max_consecutive_cycle_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--max-consecutive-cycle-errors",
                "0",
            ]
        )
    assert exc_info.value.code == 2


def test_shadow_runtime_cli_retry_setting_is_not_runtime_semantics(
    tmp_path: Path,
) -> None:
    parser = shadow_runtime_cli.build_parser()
    default_args = parser.parse_args(_base_args(tmp_path))
    tuned_args = parser.parse_args(
        [
            *_base_args(tmp_path),
            "--max-consecutive-cycle-errors",
            "9",
        ]
    )
    assert shadow_runtime_cli._runtime_config(default_args) == shadow_runtime_cli._runtime_config(
        tuned_args
    )


def test_shadow_runtime_cli_preflight_is_read_only_and_writes_atomic_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BSC_RPC_URL", "https://secret-rpc.example.invalid/key")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "clickhouse-secret")

    rpc = object()
    clickhouse = object()
    binance = object()
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: rpc)
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: clickhouse,
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: binance,
    )

    captured: dict[str, object] = {}

    def fake_preflight(
        received_rpc: object,
        received_clickhouse: object,
        received_binance: object,
        market: Market,
        canonical_database: Path,
        shadow_database: Path,
        *,
        config: ShadowRuntimeConfig,
    ) -> FakePreflightReport:
        assert received_rpc is rpc
        assert received_clickhouse is clickhouse
        assert received_binance is binance
        captured["market"] = market.symbol
        captured["canonical_database"] = canonical_database
        captured["shadow_database"] = shadow_database
        captured["config"] = config
        return FakePreflightReport()

    def must_not_run_cycle(*args: object, **kwargs: object) -> object:
        raise AssertionError("preflight must not enter the runtime cycle")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_preflight",
        fake_preflight,
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        must_not_run_cycle,
    )

    def must_not_construct_runtime_lock(path: Path) -> object:
        raise AssertionError(f"preflight must not construct runtime lock for {path}")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "ShadowRuntimeProcessLock",
        must_not_construct_runtime_lock,
    )

    def must_not_construct_lineage_lock(
        *args: object,
        **kwargs: object,
    ) -> object:
        raise AssertionError("preflight must not construct Binance lineage lock")

    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinanceLiveLineageProcessLock",
        must_not_construct_lineage_lock,
    )

    output = tmp_path / "evidence" / "preflight.json"
    assert (
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--preflight-only",
                "--preflight-output",
                str(output),
                "--purge-rounds",
                "3",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert payload == stored
    assert payload["ready"] is True
    assert payload["signing_enabled"] is False
    assert payload["funded_execution"] is False
    assert captured["market"] == "BNBUSD"
    assert captured["shadow_database"] == tmp_path / "shadow.sqlite3"
    config = captured["config"]
    assert isinstance(config, ShadowRuntimeConfig)
    assert config.inference.purge_rounds == 3
    assert not (tmp_path / "shadow.sqlite3").exists()
    assert not (tmp_path / "shadow.sqlite3.runtime-lock.sqlite3").exists()
    assert not output.with_name(output.name + ".tmp").exists()

    rendered = output.read_text(encoding="utf-8")
    assert "secret-rpc" not in rendered
    assert "clickhouse-secret" not in rendered


def test_shadow_runtime_cli_preflight_returns_two_when_not_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BSC_RPC_URL", "https://example.invalid")
    monkeypatch.setenv("CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setattr(shadow_runtime_cli, "JsonRpcClient", lambda url: object())
    monkeypatch.setattr(
        shadow_runtime_cli,
        "ClickHouseHttpClient",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "BinancePublicHttpClient",
        lambda: object(),
    )
    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_preflight",
        lambda *args, **kwargs: FakePreflightReport(ready=False),
    )

    assert (
        shadow_runtime_cli.main(
            [*_base_args(tmp_path), "--preflight-only"]
        )
        == 2
    )


def test_shadow_runtime_cli_preflight_rejects_cycle_evidence_outputs(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--preflight-only",
                "--preflight-output",
                str(tmp_path / "preflight.json"),
                "--evidence-output",
                str(tmp_path / "runtime.json"),
            ]
        )
    assert exc_info.value.code == 2


def test_shadow_runtime_cli_preflight_output_requires_preflight_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--preflight-output",
                str(tmp_path / "preflight.json"),
            ]
        )
    assert exc_info.value.code == 2


def test_shadow_runtime_cli_rejects_subsecond_polling(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main(
            [
                *_base_args(tmp_path),
                "--poll-seconds",
                "0.5",
            ]
        )
    assert exc_info.value.code == 2


def test_shadow_runtime_cli_requires_external_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("BSC_RPC_URL", raising=False)
    monkeypatch.delenv("CLICKHOUSE_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        shadow_runtime_cli.main([*_base_args(tmp_path), "--once"])
    assert exc_info.value.code == 2
