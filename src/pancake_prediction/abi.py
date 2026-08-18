from __future__ import annotations

# These selectors are pinned against observed calls to the deployed PancakeSwap
# Prediction V2 contract. Keeping the executable write surface explicit avoids
# silently accepting an arbitrary ABI/function string in the execution layer.
BET_FUNCTION_SELECTORS: dict[str, str] = {
    "bull": "0x57fb096f",  # betBull(uint256)
    "bear": "0xaa6b873a",  # betBear(uint256)
}

BET_EVENT_TOPICS: dict[str, str] = {
    "bull": "0x438122d8cff518d18388099a5181f0d17a12b4f1b55faedf6e4a6acee0060c12",
    "bear": "0x0d8c1fe3e67ab767116a81f122b83c2557a8c2564019cb7c4f83de1aeb1f1f0d",
}


def encode_bet_calldata(side: str, epoch: int) -> str:
    normalized_side = side.lower()
    if normalized_side not in BET_FUNCTION_SELECTORS:
        raise ValueError("side must be 'bull' or 'bear'")
    if epoch < 0 or epoch >= 2**256:
        raise ValueError("epoch is outside uint256")
    return BET_FUNCTION_SELECTORS[normalized_side] + epoch.to_bytes(32, "big").hex()
