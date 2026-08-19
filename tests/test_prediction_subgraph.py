from __future__ import annotations

import json
from urllib.request import Request

import pytest

from pancake_prediction_ai.prediction_subgraph import (
    PREDICTION_V2_SUBGRAPH_ID,
    PREDICTION_V2_SUBGRAPH_URL,
    PredictionSubgraphClient,
    PredictionSubgraphError,
    bnb_decimal_to_wei,
)


def _round(epoch: int) -> dict[str, object]:
    return {
        "id": str(epoch),
        "epoch": str(epoch),
        "position": "Bull",
        "failed": False,
        "startAt": str(1_000 + epoch),
        "startBlock": str(10_000 + epoch),
        "startHash": "0x" + "11" * 32,
        "lockAt": str(1_300 + epoch),
        "lockBlock": str(10_100 + epoch),
        "lockHash": "0x" + "22" * 32,
        "lockPrice": "600.12345678",
        "lockRoundId": str(50_000 + epoch),
        "closeAt": str(1_600 + epoch),
        "closeBlock": str(10_200 + epoch),
        "closeHash": "0x" + "33" * 32,
        "closePrice": "601.12345678",
        "closeRoundId": str(50_100 + epoch),
        "totalBets": "3",
        "totalAmount": "1.5",
        "bullBets": "2",
        "bullAmount": "1.0",
        "bearBets": "1",
        "bearAmount": "0.5",
    }


def _bet(identifier: str, epoch: int, *, amount: str = "0.125", position: str = "Bull") -> dict[str, object]:
    return {
        "id": identifier,
        "round": {"id": str(epoch), "epoch": str(epoch)},
        "user": {"id": "0x" + "ab" * 20},
        "hash": "0x" + "cd" * 32,
        "amount": amount,
        "position": position,
        "createdAt": "1234",
        "updatedAt": "1234",
        "block": "5678",
    }


def test_gateway_url_uses_official_prediction_subgraph_id_without_key() -> None:
    assert PREDICTION_V2_SUBGRAPH_ID == "4kRuZVKCR9dsG2ePXhLSiKw5oaw3YMJo4nAwxZbUaqVY"
    assert PREDICTION_V2_SUBGRAPH_URL.endswith(f"/subgraphs/id/{PREDICTION_V2_SUBGRAPH_ID}")
    assert "key" not in PREDICTION_V2_SUBGRAPH_URL.lower()


def test_api_key_is_authorization_header_only_and_not_exposed_on_transport_error() -> None:
    key = "secret-graph-key-value"
    seen: list[Request] = []

    def transport(request: Request, timeout: float) -> bytes:
        seen.append(request)
        raise RuntimeError(f"upstream rejected Authorization={request.get_header('Authorization')}")

    client = PredictionSubgraphClient(api_key=key, http_post=transport)
    with pytest.raises(PredictionSubgraphError) as exc_info:
        client.meta()
    assert key not in str(exc_info.value)
    assert key not in client.endpoint
    assert seen[0].get_header("Authorization") == f"Bearer {key}"
    assert seen[0].full_url == PREDICTION_V2_SUBGRAPH_URL


def test_meta_parses_indexing_state() -> None:
    def transport(request: Request, timeout: float) -> bytes:
        return json.dumps(
            {
                "data": {
                    "_meta": {
                        "block": {"number": 123, "hash": "0x" + "ef" * 32},
                        "hasIndexingErrors": False,
                    }
                }
            }
        ).encode()

    meta = PredictionSubgraphClient("k", http_post=transport).meta()
    assert meta.block_number == 123
    assert meta.block_hash == "0x" + "ef" * 32
    assert meta.has_indexing_errors is False


def test_round_pagination_uses_epoch_cursor_and_reconciles_amounts() -> None:
    calls: list[dict[str, object]] = []

    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        variables = payload["variables"]
        calls.append(variables)
        after = int(variables["after"])
        if after == 9:
            rows = [_round(10), _round(11)]
        elif after == 11:
            rows = [_round(12)]
        else:
            rows = []
        return json.dumps({"data": {"rounds": rows}}).encode()

    result = PredictionSubgraphClient("k", http_post=transport).rounds(
        from_epoch=10,
        to_epoch=12,
        page_size=2,
    )
    assert [item.epoch for item in result] == [10, 11, 12]
    assert result[0].total_amount_wei == 1_500_000_000_000_000_000
    assert result[0].bull_amount_wei == 1_000_000_000_000_000_000
    assert result[0].bear_amount_wei == 500_000_000_000_000_000
    assert result[0].complete
    assert calls == [
        {"first": 2, "after": "9", "through": "12"},
        {"first": 2, "after": "11", "through": "12"},
    ]


def test_bet_pagination_and_expected_epoch_guard() -> None:
    calls: list[str] = []

    def transport(request: Request, timeout: float) -> bytes:
        payload = json.loads(request.data or b"{}")
        after = payload["variables"]["after"]
        calls.append(after)
        if after == "":
            rows = [_bet("a", 42), _bet("b", 42, position="Bear")]
        elif after == "b":
            rows = [_bet("c", 42)]
        else:
            rows = []
        return json.dumps({"data": {"bets": rows}}).encode()

    result = PredictionSubgraphClient("k", http_post=transport).bets_for_round(
        "42",
        expected_epoch=42,
        page_size=2,
    )
    assert [item.id for item in result] == ["a", "b", "c"]
    assert result[0].amount_wei == 125_000_000_000_000_000
    assert result[1].position == "Bear"
    assert calls == ["", "b"]


def test_graphql_errors_are_hard_failure_without_echoing_key() -> None:
    key = "never-print-me"

    def transport(request: Request, timeout: float) -> bytes:
        return json.dumps({"errors": [{"message": "indexer unavailable"}]}).encode()

    with pytest.raises(PredictionSubgraphError, match="indexer unavailable") as exc_info:
        PredictionSubgraphClient(key, http_post=transport).meta()
    assert key not in str(exc_info.value)


def test_round_reconciliation_mismatch_is_rejected() -> None:
    bad = _round(10)
    bad["totalAmount"] = "9"

    def transport(request: Request, timeout: float) -> bytes:
        return json.dumps({"data": {"rounds": [bad]}}).encode()

    with pytest.raises(PredictionSubgraphError, match="amounts do not reconcile"):
        PredictionSubgraphClient("k", http_post=transport).rounds(
            from_epoch=10,
            to_epoch=10,
        )


def test_sub_wei_decimal_is_rejected() -> None:
    assert bnb_decimal_to_wei("0.000000000000000001") == 1
    with pytest.raises(PredictionSubgraphError, match="sub-wei"):
        bnb_decimal_to_wei("0.0000000000000000001")


def test_endpoint_cannot_embed_credentials_or_query_secret() -> None:
    with pytest.raises(ValueError, match="credentials/query/fragment"):
        PredictionSubgraphClient("k", endpoint="https://gateway.thegraph.com/api/subgraphs/id/x?api_key=oops")
