from __future__ import annotations

from pathlib import Path

import pytest

from pancake_prediction_ai.historical_subgraph_cli import GRAPH_API_KEY_ENV, build_parser, main


def _args(tmp_path: Path) -> list[str]:
    return [
        "--store",
        str(tmp_path / "history.sqlite"),
        "--dataset-id",
        "graph-v1",
        "--rpc-url",
        "https://example.invalid",
        "--from-epoch",
        "10",
        "--to-epoch",
        "20",
        "--decision-lead-ns",
        "10000000000",
        "--binance-latency-ns",
        "500000000",
        "--onchain-latency-ns",
        "1000000000",
        "--subgraph-latency-ns",
        "3000000000",
        "--dataset-output",
        str(tmp_path / "dataset.json"),
    ]


def test_cli_has_no_api_key_argument() -> None:
    help_text = build_parser().format_help().lower()
    assert "--api-key" not in help_text
    assert "graph_api_key" not in help_text


def test_cli_requires_graph_api_key_environment_before_network_or_store(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(GRAPH_API_KEY_ENV, raising=False)
    with pytest.raises(SystemExit, match=GRAPH_API_KEY_ENV):
        main(_args(tmp_path))
    assert not (tmp_path / "history.sqlite").exists()
    assert not (tmp_path / "dataset.json").exists()
