from __future__ import annotations

from pathlib import Path
from typing import Any

from pancake_prediction.contracts import MARKETS
from pancake_prediction.explorer_bootstrap import run_explorer_historical_bootstrap
from pancake_prediction.explorer_logs import EtherscanV2LogsClient


def _address_word(address: str) -> str:
    return "0" * 24 + address.removeprefix("0x").lower()


def _uint_word(value: int) -> str:
    return f"{value:064x}"


def _creation_input() -> str:
    words = (
        _address_word("0x1111111111111111111111111111111111111111"),
        _address_word("0x2222222222222222222222222222222222222222"),
        _address_word("0x3333333333333333333333333333333333333333"),
        _uint_word(300),
        _uint_word(30),
        _uint_word(10**15),
        _uint_word(300),
        _uint_word(300),
    )
    return "0x6000600055" + "".join(words)


class EmptyExplorer(EtherscanV2LogsClient):
    def __init__(self) -> None:
        super().__init__("fixture-secret", page_size=10, retries=1)

    def _request_page(self, params: dict[str, str]) -> list[dict[str, Any]]:
        assert params["chainid"] == "56"
        return []


class CanonicalRpcFixture:
    def __init__(self) -> None:
        self.eth_call_count = 0

    def chain_id(self) -> int:
        return 56

    def block_number(self) -> int:
        return 10_333_900

    def block(self, number: int) -> dict[str, Any]:
        return {
            "number": hex(number),
            "hash": "0x" + f"{number:064x}",
            "parentHash": "0x" + f"{max(0, number - 1):064x}",
            "timestamp": "0x1",
        }

    def get_code(self, address: str, block: int | str = "latest") -> str:
        return "0x01"

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        self.eth_call_count += 1
        raise AssertionError("explorer bootstrap must not require historical eth_call")

    def transaction_by_hash(self, tx_hash: str) -> dict[str, Any] | None:
        market = MARKETS["BNBUSD"]
        assert market.creation_tx_hash is not None
        assert tx_hash == market.creation_tx_hash
        return {
            "hash": market.creation_tx_hash,
            "to": None,
            "blockNumber": hex(10_333_825),
            "input": _creation_input(),
        }


def test_explorer_bootstrap_seeds_chainlink_from_constructor_without_eth_call(
    tmp_path: Path,
) -> None:
    canonical = CanonicalRpcFixture()
    explorer = EmptyExplorer()
    database = tmp_path / "bootstrap.sqlite3"

    report = run_explorer_historical_bootstrap(
        canonical,
        explorer,
        MARKETS["BNBUSD"],
        database,
        from_block=10_333_825,
        to_block=10_333_830,
        include_chainlink=True,
        chunk_size=100,
    )

    assert canonical.eth_call_count == 0
    assert report.deployment.oracle_address == "0x1111111111111111111111111111111111111111"
    assert report.oracle_addresses == ("0x1111111111111111111111111111111111111111",)
    assert report.chainlink_events_inserted == 0
    assert report.replay_rounds == 0
    payload = report.as_dict()
    assert payload["archive_state_required"] is False
    assert payload["historical_eth_call_required"] is False
    assert payload["canonical_block_hash_verification"] is True
    assert payload["signing_enabled"] is False
    assert payload["live_broadcast"] is False
    assert payload["explorer_manifest"]["credential_persisted"] is False
    assert "fixture-secret" not in str(payload)


def test_explorer_bootstrap_rejects_range_before_deployment(tmp_path: Path) -> None:
    canonical = CanonicalRpcFixture()
    explorer = EmptyExplorer()

    try:
        run_explorer_historical_bootstrap(
            canonical,
            explorer,
            MARKETS["BNBUSD"],
            tmp_path / "bootstrap.sqlite3",
            from_block=10_333_824,
            to_block=10_333_830,
        )
    except ValueError as exc:
        assert "before contract deployment" in str(exc)
    else:
        raise AssertionError("pre-deployment explorer bootstrap must fail closed")
