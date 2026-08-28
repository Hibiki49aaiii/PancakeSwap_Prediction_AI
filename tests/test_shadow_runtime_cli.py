from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from pancake_prediction import shadow_runtime_cli
from pancake_prediction.shadow_runtime import ShadowRuntimeConfig


@dataclass(frozen=True)
class FakeCycleReport:
    status: str = "no_eligible_target"

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
        market: object,
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
                "market": getattr(market, "symbol"),
                "canonical_database": canonical_database,
                "shadow_database": shadow_database,
                "config": config,
            }
        )
        return FakeCycleReport()

    monkeypatch.setattr(
        shadow_runtime_cli,
        "run_shadow_runtime_cycle",
        fake_cycle,
    )

    evidence = tmp_path / "evidence" / "runtime-latest.json"
    args = [
        *_base_args(tmp_path),
        "--once",
        "--evidence-output",
        str(evidence),
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
