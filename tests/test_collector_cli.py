from __future__ import annotations

from pancake_prediction_ai.collector_cli import build_parser, main
from pancake_prediction_ai.event_store import EventRecord, EventStore


def test_cli_has_no_wallet_key_or_transaction_arguments() -> None:
    parser = build_parser()
    help_text = parser.format_help().lower()
    forbidden = ("private-key", "seed", "mnemonic", "wallet", "send-transaction", "broadcast")
    assert all(term not in help_text for term in forbidden)


def test_shadow_cycle_parser_accepts_only_simulated_economic_policy_fields() -> None:
    args = build_parser().parse_args(
        [
            "--store",
            "observed.sqlite",
            "shadow-cycle-once",
            "--rpc-url",
            "http://127.0.0.1:8545",
            "--model-artifact",
            "model.json",
            "--shadow-stake-wei",
            "100",
            "--shadow-gas-cost-wei",
            "2",
            "--shadow-execution-success-probability",
            "0.75",
            "--shadow-min-expected-return",
            "0.01",
        ]
    )
    assert args.shadow_stake_wei == 100
    assert args.shadow_gas_cost_wei == 2
    assert args.shadow_execution_success_probability == 0.75
    assert args.shadow_min_expected_return == 0.01


def test_shadow_settlement_commands_parse_as_observed_workflows() -> None:
    parser = build_parser()
    single = parser.parse_args(
        [
            "--store",
            "observed.sqlite",
            "shadow-settle-round",
            "--rpc-url",
            "http://127.0.0.1:8545",
            "--round-id",
            "123",
        ]
    )
    batch = parser.parse_args(
        [
            "--store",
            "observed.sqlite",
            "shadow-settle-pending",
            "--rpc-url",
            "http://127.0.0.1:8545",
            "--max-rounds",
            "20",
        ]
    )
    assert single.round_id == 123
    assert batch.max_rounds == 20


def test_shadow_summary_command_handles_empty_observed_store(tmp_path, capsys) -> None:
    path = tmp_path / "empty.sqlite"
    result = main(["--store", str(path), "shadow-summary"])
    assert result == 0
    output = capsys.readouterr().out
    assert "decisions=0" in output
    assert "settled=0" in output


def test_verify_store_command_returns_zero_for_valid_chain(tmp_path, capsys) -> None:
    path = tmp_path / "events.sqlite"
    with EventStore(path) as store:
        store.append(EventRecord("a", "test", "topic", 1, 1, {"x": 1}))
    result = main(["--store", str(path), "verify-store"])
    assert result == 0
    assert "OK" in capsys.readouterr().out


def test_verify_store_command_returns_two_for_tampered_chain(tmp_path, capsys) -> None:
    path = tmp_path / "events.sqlite"
    with EventStore(path) as store:
        store.append(EventRecord("a", "test", "topic", 1, 1, {"x": 1}))
        store._conn.execute("UPDATE events SET payload_json = ? WHERE event_id = ?", ('{"x":999}', "a"))
        store._conn.commit()
    result = main(["--store", str(path), "verify-store"])
    assert result == 2
    assert "FAILED" in capsys.readouterr().out
