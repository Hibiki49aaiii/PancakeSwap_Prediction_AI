from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from pancake_prediction_ai.evidence_gate import Evidence, EvidenceKind, EvidenceOrigin
from pancake_prediction_ai.readiness_cli import (
    build_parser,
    main,
    run_stage5a_command,
    run_stage5b_command,
    run_stage5b_execution_command,
)


BLOCK_HASH = "0x" + "ab" * 32
BYTECODE = "0x60016000"


class FakeLocalForkRpc:
    def __init__(self) -> None:
        self.block = 0x1234
        self.base_block = self.block

    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return "0x38"
        if method == "eth_blockNumber":
            return hex(self.block)
        if method == "eth_getBlockByNumber":
            requested = int(str(params[0]), 16)
            return {"number": hex(requested), "hash": BLOCK_HASH}
        if method == "eth_getCode":
            return BYTECODE
        if method == "evm_mine":
            self.block += 1
            return True
        if method == "anvil_reset":
            self.block = self.base_block
            return True
        raise KeyError(method)


class FakeUpstreamRpc:
    def __call__(self, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return "0x38"
        if method == "eth_getBlockByNumber":
            requested = int(str(params[0]), 16)
            return {"number": hex(requested), "hash": BLOCK_HASH}
        if method == "eth_getCode":
            return BYTECODE
        raise KeyError(method)


def _fixture_evidence() -> Evidence:
    payload: dict[str, object] = {
        "schema": "stage5b_verified_local_bsc_fork_execution_v4",
        "probe_type": "verified_local_bsc_fork_prediction_execution",
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return Evidence(
        EvidenceKind.STAGE5B_FORK,
        EvidenceOrigin.OBSERVED,
        True,
        digest,
        "2026-08-19T22:35:00+09:00",
        payload,
    )


def test_stage5a_command_writes_reloadable_evidence(tmp_path) -> None:
    output = tmp_path / "stage5a.json"
    evidence = run_stage5a_command(
        database=tmp_path / "stage5a.sqlite3",
        output=output,
        required_confirmations=3,
    )
    assert evidence.passed
    assert Evidence.from_path(output) == evidence


def test_stage5b_provenance_command_writes_verified_reloadable_v2_evidence(tmp_path) -> None:
    output = tmp_path / "stage5b-provenance.json"
    evidence = run_stage5b_command(
        local_rpc=FakeLocalForkRpc(),
        upstream_rpc=FakeUpstreamRpc(),
        prediction_contract="0x1111111111111111111111111111111111111111",
        chainlink_contract="0x2222222222222222222222222222222222222222",
        output=output,
    )
    assert evidence.passed
    assert evidence.payload["schema"] == "stage5b_verified_local_bsc_fork_v2"
    assert evidence.payload["upstream_verified"] is True
    assert Evidence.from_path(output) == evidence


def test_stage5b_execute_parser_exposes_local_execution_controls(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "stage5b-execute-fork",
            "--local-rpc-url",
            "http://127.0.0.1:8545",
            "--upstream-rpc-url",
            "https://example.invalid",
            "--prediction-contract",
            "0x1111111111111111111111111111111111111111",
            "--chainlink-contract",
            "0x2222222222222222222222222222222222222222",
            "--stake-wei",
            "123",
            "--gas-limit",
            "456000",
            "--min-window-margin-seconds",
            "5",
            "--output",
            str(tmp_path / "stage5b.json"),
        ]
    )
    assert args.command == "stage5b-execute-fork"
    assert args.stake_wei == 123
    assert args.gas_limit == 456000
    assert args.min_window_margin_seconds == 5


def test_stage5b_execution_command_composes_provenance_execution_and_writer(
    tmp_path, monkeypatch
) -> None:
    from pancake_prediction_ai import readiness_cli

    calls: list[tuple[str, object]] = []
    fork_result = SimpleNamespace(verified_passed=True)
    execution_result = SimpleNamespace(passed=True)
    expected = _fixture_evidence()

    def fake_probe(local_rpc, upstream_rpc, *, prediction_contract: str, chainlink_contract: str):
        calls.append(("probe", (prediction_contract, chainlink_contract)))
        return fork_result

    def fake_execute(local_rpc, *, fork_result, stake_wei, gas_limit, min_window_margin_seconds):
        calls.append(("execute", (stake_wei, gas_limit, min_window_margin_seconds)))
        return execution_result

    def fake_build(fork, execution):
        assert fork is fork_result
        assert execution is execution_result
        calls.append(("build", True))
        return expected

    def fake_write(evidence: Evidence, path: str | Path):
        assert evidence is expected
        calls.append(("write", Path(path)))
        return Path(path)

    monkeypatch.setattr(readiness_cli, "probe_verified_local_bsc_fork", fake_probe)
    monkeypatch.setattr(readiness_cli, "run_stage5b_prediction_execution_probe", fake_execute)
    monkeypatch.setattr(readiness_cli, "make_stage5b_execution_evidence", fake_build)
    monkeypatch.setattr(readiness_cli, "write_stage5b_evidence", fake_write)

    output = tmp_path / "stage5b-v4.json"
    result = run_stage5b_execution_command(
        local_rpc=lambda method, params: None,
        upstream_rpc=lambda method, params: None,
        prediction_contract="0x1111111111111111111111111111111111111111",
        chainlink_contract="0x2222222222222222222222222222222222222222",
        output=output,
        stake_wei=123,
        gas_limit=456000,
        min_window_margin_seconds=5,
    )
    assert result is expected
    assert calls == [
        (
            "probe",
            (
                "0x1111111111111111111111111111111111111111",
                "0x2222222222222222222222222222222222222222",
            ),
        ),
        ("execute", (123, 456000, 5)),
        ("build", True),
        ("write", output),
    ]


def test_stage5a_cli_returns_success_and_creates_artifact(tmp_path, capsys) -> None:
    output = tmp_path / "stage5a.json"
    code = main(
        [
            "stage5a-drill",
            "--database",
            str(tmp_path / "stage5a.sqlite3"),
            "--output",
            str(output),
            "--required-confirmations",
            "2",
        ]
    )
    assert code == 0
    assert output.exists()
    printed = capsys.readouterr().out
    assert "stage5a-evidence" in printed
    assert "passed=True" in printed
