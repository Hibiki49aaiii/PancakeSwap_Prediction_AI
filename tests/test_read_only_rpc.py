from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from pancake_prediction_ai.read_only_rpc import (
    DEFAULT_USER_AGENT,
    ReadOnlyJsonRpcClient,
    RpcError,
)


def test_read_only_rpc_parses_chain_id_and_sets_explicit_http_headers() -> None:
    seen: list[dict[str, object]] = []

    def transport(request: Request, timeout: float) -> bytes:
        assert timeout == 3.0
        assert request.get_header("Accept") == "application/json"
        assert request.get_header("Content-type") == "application/json"
        assert request.get_header("User-agent") == DEFAULT_USER_AGENT
        payload = json.loads(request.data or b"{}")
        seen.append(payload)
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": "0x38"}).encode()

    client = ReadOnlyJsonRpcClient("http://127.0.0.1:8545", timeout_seconds=3.0, transport=transport)
    assert client.chain_id() == 56
    assert seen[0]["method"] == "eth_chainId"


def test_eth_getlogs_singleton_topic_or_is_normalized_before_transport() -> None:
    seen_topics: list[object] = []

    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        topic0 = payload["params"][0]["topics"][0]
        seen_topics.append(topic0)
        if isinstance(topic0, list):
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": []}).encode()

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    assert client.call(
        "eth_getLogs",
        [{"fromBlock": "0x1", "toBlock": "0x2", "topics": [["0xabc"]]}],
    ) == []
    assert seen_topics == ["0xabc"]


def test_eth_getlogs_http_403_is_split_until_provider_accepts_range() -> None:
    seen_ranges: list[tuple[int, int]] = []

    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        filter_ = payload["params"][0]
        start = int(filter_["fromBlock"], 16)
        end = int(filter_["toBlock"], 16)
        seen_ranges.append((start, end))
        if end - start + 1 > 2:
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)
        rows = [
            {"blockNumber": hex(block), "logIndex": "0x0", "transactionHash": f"0x{block:064x}"}
            for block in range(start, end + 1)
        ]
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": rows}).encode()

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    result = client.call(
        "eth_getLogs",
        [{"fromBlock": "0xa", "toBlock": "0xd", "topics": [["0xabc"]]}],
    )
    assert [int(row["blockNumber"], 16) for row in result] == [10, 11, 12, 13]
    assert seen_ranges == [(10, 13), (10, 11), (12, 13)]


def test_eth_getlogs_http_403_splits_topic_or_before_block_range() -> None:
    seen: list[tuple[int, int, tuple[str, ...]]] = []

    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        filter_ = payload["params"][0]
        start = int(filter_["fromBlock"], 16)
        end = int(filter_["toBlock"], 16)
        raw_topic0 = filter_["topics"][0]
        alternatives = (raw_topic0,) if isinstance(raw_topic0, str) else tuple(raw_topic0)
        seen.append((start, end, alternatives))
        if len(alternatives) > 1:
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)
        topic = alternatives[0]
        log_index = {"0xa": "0x2", "0xb": "0x1", "0xc": "0x0"}[topic]
        row = {
            "blockNumber": "0x64",
            "logIndex": log_index,
            "transactionHash": "0x" + topic[2:].rjust(64, "0"),
            "topics": [topic],
        }
        return json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": [row]}).encode()

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    result = client.call(
        "eth_getLogs",
        [{"fromBlock": "0x1", "toBlock": "0x3e8", "topics": [["0xa", "0xb", "0xc"]]}],
    )
    assert [row["topics"][0] for row in result] == ["0xc", "0xb", "0xa"]
    assert seen == [
        (1, 1000, ("0xa", "0xb", "0xc")),
        (1, 1000, ("0xa",)),
        (1, 1000, ("0xb", "0xc")),
        (1, 1000, ("0xb",)),
        (1, 1000, ("0xc",)),
    ]


def test_eth_getlogs_rpc_range_limit_is_split_but_other_rpc_errors_are_not() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        filter_ = payload["params"][0]
        start = int(filter_["fromBlock"], 16)
        end = int(filter_["toBlock"], 16)
        if end > start:
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32602, "message": "eth_getLogs is limited to 0 - 1 blocks range"},
                }
            ).encode()
        return json.dumps(
            {"jsonrpc": "2.0", "id": payload["id"], "result": [{"blockNumber": hex(start)}]}
        ).encode()

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    result = client.call("eth_getLogs", [{"fromBlock": "0x1", "toBlock": "0x2"}])
    assert [row["blockNumber"] for row in result] == ["0x1", "0x2"]


@pytest.mark.parametrize(
    "method",
    [
        "eth_sendRawTransaction",
        "eth_sendTransaction",
        "personal_sendTransaction",
        "wallet_sendCalls",
        "anvil_setBalance",
    ],
)
def test_write_or_mutating_rpc_methods_are_rejected_before_transport(method: str) -> None:
    called = False

    def transport(request: Request, timeout: float) -> bytes:
        nonlocal called
        called = True
        return b"{}"

    client = ReadOnlyJsonRpcClient("http://127.0.0.1:8545", transport=transport)
    with pytest.raises(PermissionError, match="outside read-only boundary"):
        client.call(method, [])
    assert not called


def test_rpc_error_is_not_silently_converted_to_result() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": -32000, "message": "node unavailable"},
            }
        ).encode()

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    with pytest.raises(RpcError, match="node unavailable"):
        client.block_number()


def test_response_id_mismatch_is_rejected() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        return b'{"jsonrpc":"2.0","id":999,"result":"0x38"}'

    client = ReadOnlyJsonRpcClient("https://example.invalid", transport=transport)
    with pytest.raises(RpcError, match="id mismatch"):
        client.chain_id()
