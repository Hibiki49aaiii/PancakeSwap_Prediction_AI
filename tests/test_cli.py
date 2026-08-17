import json

import pytest

from pancake_prediction.cli import main


def test_cli_status_exposes_research_safety_boundary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stage"] == "v0.7-alpha-research"
    assert payload["live_broadcast"] is False
    assert payload["signing_enabled"] is False
    assert payload["markets"] == ["BNBUSD", "BTCUSD", "ETHUSD"]


def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "pcs-prediction" in capsys.readouterr().out
