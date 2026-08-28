import json
from pathlib import Path

import pytest

from pancake_prediction import cli
from pancake_prediction.contracts import Market
from pancake_prediction.historical_preflight import HistoricalPreflightResult
from pancake_prediction.replay import ReplaySnapshot, RoundRecord
from pancake_prediction.rpc_probe import ArchiveProbeResult

SENDER = "0x" + "11" * 20


def _archive_probe(market: Market, block_number: int) -> ArchiveProbeResult:
    return ArchiveProbeResult(
        chain_id=56,
        market=market.symbol,
        block_number=block_number,
        block_hash="0x" + "aa" * 32,
        block_timestamp=1_700_000_000,
        oracle_address="0x" + "11" * 20,
        prediction_code_present=True,
        oracle_code_present=True,
    )


class FakeBootstrapResult:
    def as_dict(self) -> dict[str, object]:
        return {
            "market": "BNBUSD",
            "database": "history.sqlite3",
            "collection_range": {"from_block": 10_333_825, "to_block": 69_999_936},
            "replay_rounds": 123,
        }


def test_cli_status_exposes_research_safety_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "v0.7-alpha-research"
    assert payload["live_broadcast"] is False
    assert payload["signing_enabled"] is False
    assert payload["fork_local_broadcast"] is True
    assert payload["fork_rpc_loopback_only"] is True
    assert payload["markets"] == ["BNBUSD", "BTCUSD", "ETHUSD"]


def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "pcs-prediction" in capsys.readouterr().out


def test_cli_rpc_probe_uses_env_without_printing_rpc_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_probe(
        rpc: object,
        market: Market,
        block_number: int,
    ) -> ArchiveProbeResult:
        del rpc
        return _archive_probe(market, block_number)

    monkeypatch.setattr(cli, "probe_archive_state", fake_probe)
    monkeypatch.setenv("BSC_RPC_URL", "https://secret-token.example.invalid")
    assert cli.main(["rpc-probe", "--market", "BNBUSD", "--block", "10333825"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["market"] == "BNBUSD"
    assert payload["block_number"] == 10_333_825
    assert "secret-token" not in output


def test_cli_historical_preflight_discovers_oldest_required_block_without_url_leak(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_preflight(
        rpc: object,
        market: Market,
    ) -> HistoricalPreflightResult:
        del rpc
        return HistoricalPreflightResult(
            market=market.symbol,
            head_block=70_000_000,
            deployment_block=10_333_825,
            archive_probe=_archive_probe(market, 10_333_825),
        )

    monkeypatch.setattr(cli, "run_historical_preflight", fake_preflight)
    monkeypatch.setenv("BSC_RPC_URL", "https://secret-token.example.invalid")
    assert cli.main(["historical-preflight", "--market", "BNBUSD"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["deployment_block"] == 10_333_825
    assert payload["archive_probe"]["block_number"] == 10_333_825
    assert "secret-token" not in output


def test_cli_historical_bootstrap_uses_confirmed_defaults_without_url_leak(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_bootstrap(
        rpc: object,
        market: Market,
        database: object,
        **kwargs: object,
    ) -> FakeBootstrapResult:
        del rpc, market, database
        assert kwargs["confirmations"] == 64
        assert kwargs["include_chainlink"] is True
        assert kwargs["prediction_analytic_only"] is True
        return FakeBootstrapResult()

    monkeypatch.setattr(cli, "run_historical_bootstrap", fake_bootstrap)
    monkeypatch.setenv("BSC_RPC_URL", "https://secret-token.example.invalid")
    args = ["historical-bootstrap", "--market", "BNBUSD", "--db", "history.sqlite3"]
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["replay_rounds"] == 123
    assert "secret-token" not in output


def test_cli_fork_create_bet_intent_is_persistent_and_does_not_need_rpc(
    tmp_path: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = str(tmp_path) + "/fork.sqlite3"
    args = [
        "fork-create-bet-intent",
        "--db",
        database,
        "--market",
        "BNBUSD",
        "--sender",
        SENDER,
        "--epoch",
        "123456",
        "--side",
        "bull",
        "--stake-wei",
        "1000000000000000",
    ]
    assert cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "created"
    assert payload["nonce"] is None
    assert payload["sender"] == SENDER
    assert payload["value_wei"] == 10**15

    assert cli.main(args) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["id"] == payload["id"]


def test_cli_fork_opposite_side_same_wallet_round_is_rejected(
    tmp_path: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = str(tmp_path) + "/fork.sqlite3"
    base = [
        "fork-create-bet-intent",
        "--db",
        database,
        "--market",
        "BNBUSD",
        "--sender",
        SENDER,
        "--epoch",
        "123456",
        "--stake-wei",
        "1000000000000000",
    ]
    assert cli.main([*base, "--side", "bull"]) == 0
    capsys.readouterr()
    with pytest.raises(ValueError, match="different payload"):
        cli.main([*base, "--side", "bear"])


def test_cli_fork_transaction_commands_reject_non_loopback_rpc(tmp_path: object) -> None:
    database = str(tmp_path) + "/fork.sqlite3"
    with pytest.raises(ValueError, match="loopback"):
        cli.main(
            [
                "fork-prepare-account",
                "--fork-rpc-url",
                "https://bsc-dataseed.binance.org",
                "--sender",
                SENDER,
                "--balance-wei",
                "1",
            ]
        )
    with pytest.raises(ValueError, match="loopback"):
        cli.main(
            [
                "fork-submit-intent",
                "--fork-rpc-url",
                "https://bsc-dataseed.binance.org",
                "--db",
                database,
                "--intent-id",
                "1",
            ]
        )


def test_cli_rpc_probe_requires_rpc_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_RPC_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["rpc-probe", "--market", "BNBUSD", "--block", "10333825"])
    assert exc_info.value.code == 2


def test_cli_historical_preflight_requires_rpc_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_RPC_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["historical-preflight", "--market", "BNBUSD"])
    assert exc_info.value.code == 2


def test_cli_historical_bootstrap_requires_rpc_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_RPC_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["historical-bootstrap", "--market", "BNBUSD", "--db", "x.sqlite3"])
    assert exc_info.value.code == 2

def test_cli_shadow_ledger_prediction_settlement_and_audit(
    tmp_path: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = str(tmp_path)
    database = root + "/shadow.sqlite3"
    prediction_path = root + "/prediction.json"
    settlement_path = root + "/settlement.json"

    prediction = {
        "market": "BNBUSD",
        "epoch": 100,
        "decision_timestamp_ms": 31_000_000,
        "model_id": "shadow-wf-v1",
        "feature_set_id": "full-v1",
        "raw_probability_ppm": 620_000,
        "calibrated_probability_ppm": 600_000,
        "expected_value_wei": 1234,
        "action": "bull",
        "feature_digest": "a" * 64,
        "train_max_epoch": 97,
        "metadata": {"source": "cli-test"},
    }
    settlement = {
        "market": "BNBUSD",
        "epoch": 100,
        "settled_timestamp_ms": 31_300_000,
        "outcome": "bull",
        "result_source_digest": "b" * 64,
        "realized_pnl_wei": 100,
        "metadata": {"source": "canonical-test"},
    }
    Path(prediction_path).write_text(json.dumps(prediction), encoding="utf-8")
    Path(settlement_path).write_text(json.dumps(settlement), encoding="utf-8")

    assert cli.main(["shadow-ledger-init", "--db", database]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["event_count"] == 0
    assert initialized["integrity_ready"] is True

    assert (
        cli.main(
            [
                "shadow-append-prediction",
                "--db",
                database,
                "--record",
                prediction_path,
            ]
        )
        == 0
    )
    prediction_event = json.loads(capsys.readouterr().out)
    assert prediction_event["kind"] == "prediction"
    assert prediction_event["sequence"] == 1

    assert (
        cli.main(
            [
                "shadow-append-settlement",
                "--db",
                database,
                "--record",
                settlement_path,
            ]
        )
        == 0
    )
    settlement_event = json.loads(capsys.readouterr().out)
    assert settlement_event["kind"] == "settlement"
    assert settlement_event["sequence"] == 2
    assert settlement_event["previous_digest"] == prediction_event["event_digest"]

    assert cli.main(["shadow-ledger-audit", "--db", database]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit["integrity_ready"] is True
    assert audit["prediction_count"] == 1
    assert audit["settlement_count"] == 1
    assert audit["brier_score"] == pytest.approx(0.16)
    assert audit["directional_accuracy"] == 1.0
    assert audit["observed_pnl_wei"] == 100
    assert audit["profitability_gate_eligible"] is False
    assert audit["signing_enabled"] is False
    assert audit["live_broadcast"] is False

    assert (
        cli.main(
            [
                "shadow-campaign-gate",
                "--db",
                database,
                "--min-predictions",
                "1",
                "--min-settlements",
                "1",
                "--min-probability-scored",
                "1",
                "--min-actionable-predictions",
                "1",
                "--min-decision-span-seconds",
                "0",
                "--max-unresolved-ppm",
                "0",
                "--allow-single-direction",
            ]
        )
        == 0
    )
    gate = json.loads(capsys.readouterr().out)
    assert gate["gate_ready"] is True
    assert gate["campaign_digest"]
    assert gate["profitability_gate_eligible"] is False
    assert gate["signing_enabled"] is False
    assert gate["live_broadcast"] is False


def test_cli_shadow_ledger_rejects_negative_purge_rounds(
    tmp_path: object,
) -> None:
    database = str(tmp_path) + "/shadow.sqlite3"
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "shadow-ledger-audit",
                "--db",
                database,
                "--purge-rounds",
                "-1",
            ]
        )
    assert exc_info.value.code == 2

def test_cli_shadow_reconcile_uses_canonical_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shadow_db = tmp_path / "shadow.sqlite3"
    prediction_path = tmp_path / "prediction.json"
    prediction_path.write_text(
        json.dumps(
            {
                "market": "BNBUSD",
                "epoch": 10,
                "decision_timestamp_ms": 1_280_000,
                "model_id": "shadow-wf-v1",
                "feature_set_id": "full-v1",
                "raw_probability_ppm": 620_000,
                "calibrated_probability_ppm": 600_000,
                "expected_value_wei": 10,
                "action": "bull",
                "feature_digest": "a" * 64,
                "train_max_epoch": 7,
                "metadata": {
                    "stake_wei": 100,
                    "bet_gas_wei": 2,
                    "claim_gas_wei": 1,
                    "treasury_fee_bps": 300,
                },
            }
        ),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "shadow-append-prediction",
                "--db",
                str(shadow_db),
                "--record",
                str(prediction_path),
            ]
        )
        == 0
    )
    capsys.readouterr()

    round_record = RoundRecord(
        epoch=10,
        start_block=100,
        start_timestamp=1_000,
        lock_block=200,
        lock_timestamp=1_300,
        lock_round_id=1_000,
        lock_price=30_000_000_000,
        end_block=300,
        end_timestamp=1_600,
        close_round_id=2_000,
        close_price=30_100_000_000,
        bull_amount_wei=1_000,
        bear_amount_wei=1_000,
        total_amount_wei=2_000,
        bet_count=10,
        reward_base_cal_amount_wei=1_000,
        reward_amount_wei=1_940,
        treasury_amount_wei=60,
        label="bull",
        issues=(),
    )
    replay = ReplaySnapshot(
        format_version=1,
        market="BNBUSD",
        input_digest="b" * 64,
        rounds=(round_record,),
    )
    monkeypatch.setattr(
        cli,
        "build_replay_snapshot",
        lambda path, market: replay,
    )

    assert (
        cli.main(
            [
                "shadow-reconcile",
                "--shadow-db",
                str(shadow_db),
                "--canonical-db",
                str(tmp_path / "canonical.sqlite3"),
                "--market",
                "BNBUSD",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["reconciliation"]["appended_settlement_count"] == 1
    assert payload["reconciliation"]["unresolved_count"] == 0
    assert payload["audit"]["integrity_ready"] is True
    assert payload["audit"]["settlement_count"] == 1
    assert payload["audit"]["observed_pnl_wei"] == 82

    assert (
        cli.main(
            [
                "shadow-reconcile",
                "--shadow-db",
                str(shadow_db),
                "--canonical-db",
                str(tmp_path / "canonical.sqlite3"),
                "--market",
                "BNBUSD",
            ]
        )
        == 0
    )
    retry = json.loads(capsys.readouterr().out)
    assert retry["reconciliation"]["appended_settlement_count"] == 0
    assert retry["reconciliation"]["existing_settlement_count"] == 1

