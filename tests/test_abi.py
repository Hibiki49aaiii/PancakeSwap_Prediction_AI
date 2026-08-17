from pancake_prediction.abi import PREDICTION_EVENTS, decode_event, function_selector


def _topic_uint(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _topic_address(address: str) -> str:
    raw = bytes.fromhex(address.removeprefix("0x"))
    return "0x" + (b"\x00" * 12 + raw).hex()


def test_decode_bet_bull() -> None:
    sender = "0x1111111111111111111111111111111111111111"
    spec = next(item for item in PREDICTION_EVENTS if item.name == "BetBull")
    log = {
        "topics": [spec.topic0, _topic_address(sender), _topic_uint(42)],
        "data": "0x" + (10**18).to_bytes(32, "big").hex(),
    }
    decoded = decode_event(log, PREDICTION_EVENTS)
    assert decoded is not None
    name, values = decoded
    assert name == "BetBull"
    assert values == {"sender": sender, "epoch": 42, "amount": 10**18}


def test_keccak_selector_matches_ethereum_transfer() -> None:
    assert function_selector("transfer(address,uint256)") == "0xa9059cbb"
