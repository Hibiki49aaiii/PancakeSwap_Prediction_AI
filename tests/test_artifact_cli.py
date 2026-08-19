from __future__ import annotations

import pytest

from pancake_prediction_ai.artifact_cli import build_parser, main


def test_artifact_cli_exposes_reproducible_pipeline_commands() -> None:
    parser = build_parser()
    help_text = parser.format_help().lower()
    assert "build-dataset" in help_text
    assert "evaluate-oos" in help_text
    assert "promote-model" in help_text


def test_artifact_cli_has_no_wallet_signing_or_broadcast_arguments() -> None:
    parser = build_parser()
    forbidden = ("private-key", "seed", "mnemonic", "wallet", "sign", "broadcast", "send-transaction")
    root = parser.format_help().lower()
    sub_actions = next(action for action in parser._actions if action.dest == "command")
    texts = [root]
    for subparser in sub_actions.choices.values():
        texts.append(subparser.format_help().lower())
    combined = "\n".join(texts)
    assert all(term not in combined for term in forbidden)


def test_build_dataset_requires_explicit_latency_assumptions() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "build-dataset",
                "--store", "history.sqlite",
                "--dataset-id", "d1",
                "--decision-lead-ns", "100",
                "--output", "dataset.json",
            ]
        )


@pytest.mark.parametrize("command", ["evaluate-oos", "promote-model"])
def test_model_commands_reject_negative_l2_before_artifact_work(command: str, tmp_path) -> None:
    dataset = tmp_path / "missing.json"
    output = tmp_path / "out.json"
    if command == "evaluate-oos":
        argv = [
            command,
            "--dataset", str(dataset),
            "--output", str(output),
            "--min-train-size", "10",
            "--test-size", "2",
            "--purge-size", "1",
            "--calibration-size", "2",
            "--l2", "-1",
        ]
    else:
        argv = [
            command,
            "--dataset", str(dataset),
            "--output", str(output),
            "--training-cutoff-ns", "100",
            "--calibration-size", "2",
            "--l2", "-1",
        ]
    with pytest.raises(SystemExit, match="--l2 must be non-negative"):
        main(argv)
