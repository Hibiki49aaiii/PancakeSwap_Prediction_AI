from __future__ import annotations

from pancake_prediction_ai.evidence_gate import Evidence
from pancake_prediction_ai.readiness_cli import main, run_stage5a_command, run_stage5b_command


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


def test_stage5a_command_writes_reloadable_evidence(tmp_path) -> None:
    output = tmp_path / "stage5a.json"
    evidence = run_stage5a_command(
        database=tmp_path / "stage5a.sqlite3",
        output=output,
        required_confirmations=3,
    )
    assert evidence.passed
    assert Evidence.from_path(output) == evidence


def test_stage5b_command_writes_verified_reloadable_evidence(tmp_path) -> None:
    output = tmp_path / "stage5b.json"
    evidence = run_stage5b_command(
        local_rpc=FakeLocalForkRpc(),
        upstream_rpc=FakeUpstreamRpc(),
        prediction_contract="0x1111111111111111111111111111111111111111",
        chainlink_contract="0x2222222222222222222222222222222222222222",
        output=output,
    )
    assert evidence.passed
    assert evidence.payload["upstream_verified"] is True
    assert Evidence.from_path(output) == evidence


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
