from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pancake_prediction import shadow_runtime_cli
from pancake_prediction.contracts import Market
from pancake_prediction.shadow_runtime import ShadowRuntimeConfig


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
        lambda *args, **kwargs: object(),
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
