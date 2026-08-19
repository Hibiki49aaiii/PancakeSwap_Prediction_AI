from __future__ import annotations

from pancake_prediction_ai.collector_cli import build_parser, main
from pancake_prediction_ai.event_store import EventRecord, EventStore


def test_cli_has_no_wallet_key_or_transaction_arguments() -> None:
    parser = build_parser()
    help_text = parser.format_help().lower()
    forbidden = ("private-key", "seed", "mnemonic", "wallet", "send-transaction", "broadcast")
    assert all(term not in help_text for term in forbidden)


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
