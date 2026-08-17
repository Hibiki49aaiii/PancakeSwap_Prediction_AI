import json

import pytest

from pancake_prediction import cli
from pancake_prediction.contracts import Market
from pancake_prediction.rpc_probe import ArchiveProbeResult


def test_cli_status_exposes_research_safety_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "v0.7-alpha-research"
    assert payload["live_broadcast"] is False
    assert payload["signing_enabled"] is False
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

    monkeypatch.setattr(cli, "probe_archive_state", fake_probe)
    monkeypatch.setenv("BSC_RPC_URL", "https://secret-token.example.invalid")
    assert cli.main(["rpc-probe", "--market", "BNBUSD", "--block", "10333825"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["market"] == "BNBUSD"
    assert payload["block_number"] == 10_333_825
    assert "secret-token" not in output


def test_cli_rpc_probe_requires_rpc_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_RPC_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["rpc-probe", "--market", "BNBUSD", "--block", "10333825"])
    assert exc_info.value.code == 2
